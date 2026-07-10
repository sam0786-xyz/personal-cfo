"""Firestore-backed state.

Layout:
- ``cfo_state/wallet``      — hot document: balance, debts, rules, and the
  most recent ``MAX_WALLET_TRANSACTIONS`` transactions (Firestore documents
  cap at 1 MiB, so the list must stay bounded).
- ``cfo_ledger/{txn_id}``   — append-only ledger holding every transaction.
- ``cfo_state/gmail_sync``  — Gmail watch/history cursor and dedup window.

All read-modify-write cycles run inside Firestore transactions so the Gmail
webhook and the voice agent cannot clobber each other's writes.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from google.cloud import firestore

from app import config
from app.classification import normalize_merchant_rules
from app.config import IST
from app.normalization import normalize_transaction_record

logger = logging.getLogger("personal-cfo")

_db = None


def get_db():
    global _db
    if _db is None:
        _db = firestore.Client(project=config.GCP_PROJECT) if config.GCP_PROJECT else firestore.Client()
    return _db


def wallet_doc():
    return get_db().collection("cfo_state").document("wallet")


def gmail_sync_doc():
    return get_db().collection("cfo_state").document("gmail_sync")


def ledger_collection():
    return get_db().collection("cfo_ledger")


def run_transaction(callback: Callable[[Any], Any]):
    """Run ``callback(transaction)`` atomically. Kept as a single seam so
    tests can substitute an in-memory store."""
    transaction = get_db().transaction()

    @firestore.transactional
    def _inner(txn):
        return callback(txn)

    return _inner(transaction)


DEFAULT_SYNC_STATE = {
    "last_history_id": None,
    "watch_expiration": None,
    "label_id": None,
    "processed_message_ids": [],
    "last_sync_at": None,
    "total_synced": 0,
}


def normalize_state(state: dict | None) -> tuple[dict, bool]:
    """Normalize a raw wallet document: backfill transaction identities,
    dedupe rules, accrue liquid-asset interest, and compute derived metrics."""
    state = state or {}
    current_balance = int(state.get("current_balance", config.DEFAULT_BALANCE))
    transactions = state.get("transactions") or []
    normalized_transactions = []
    state_changed = False
    for index, transaction in enumerate(transactions):
        normalized_transaction, changed = normalize_transaction_record(transaction, index)
        normalized_transactions.append(normalized_transaction)
        state_changed = state_changed or changed
    transactions = normalized_transactions
    merchant_rules = normalize_merchant_rules(state.get("merchant_rules") or [])
    if merchant_rules != (state.get("merchant_rules") or []):
        state_changed = True
    owed_by = state.get("owed_by") or {}
    owed_by = {
        str(name).strip().title(): int(amount)
        for name, amount in owed_by.items()
        if str(name).strip() and int(amount) > 0
    }

    # Fractional liquidity model (the 90% rule): principal accrues daily
    # interest; 90% is treated as spendable, 10% as untouchable core.
    liquid_state = state.get("liquid_assets") or {
        "principal": config.LIQUID_SEED_PRINCIPAL,
        "stamp_duty_paid": False,
        "last_accrual_date": datetime.now(IST).date().isoformat(),
    }

    if not liquid_state.get("stamp_duty_paid", False):
        liquid_state["principal"] -= liquid_state["principal"] * config.STAMP_DUTY_RATE
        liquid_state["stamp_duty_paid"] = True
        state_changed = True

    last_accrual = liquid_state.get("last_accrual_date") or datetime.now(IST).date().isoformat()
    last_date = datetime.fromisoformat(last_accrual).date()
    today = datetime.now(IST).date()
    days_passed = (today - last_date).days

    if days_passed > 0:
        daily_rate = config.LIQUID_APY / 365.0
        liquid_state["principal"] *= (1 + daily_rate) ** days_passed
        liquid_state["last_accrual_date"] = today.isoformat()
        state_changed = True

    nav = liquid_state["principal"]
    spendable = nav * 0.90
    core = nav * 0.10

    # Opportunity cost of idle cash at the configured APY.
    dead_capital_opportunity_cost = round((current_balance * config.LIQUID_APY) / 365.0, 2)

    synthetic_state = state.get("synthetic_assets") or {
        "axis_edge_points": config.SEED_AXIS_EDGE_POINTS,
        "github_credits_usd": config.SEED_GITHUB_CREDITS_USD,
    }

    synthetic_metrics = {
        "axis_edge_inr": int(synthetic_state.get("axis_edge_points", 0) * config.AXIS_EDGE_POINT_INR),
        "github_credits_inr": int(synthetic_state.get("github_credits_usd", 0) * config.USD_INR),
    }
    synthetic_metrics["total_cash_equivalent"] = synthetic_metrics["axis_edge_inr"] + synthetic_metrics["github_credits_inr"]

    gcp_billing = state.get("gcp_billing") or {"vertex_daily_burn_usd": config.DEFAULT_API_BURN_USD}
    daily_burn = float(gcp_billing.get("vertex_daily_burn_usd", 0) or 0)
    vertex_runway_days = -1
    if daily_burn > 0:
        vertex_runway_days = int(synthetic_state.get("github_credits_usd", 0) / daily_burn)
    gcp_billing["vertex_runway_days"] = vertex_runway_days

    normalized = {
        "current_balance": current_balance,
        "transactions": transactions,
        "merchant_rules": merchant_rules,
        "owed_by": owed_by,
        "liquid_assets": liquid_state,
        "liquid_metrics": {
            "nav": round(nav, 2),
            "spendable": round(spendable, 2),
            "core": round(core, 2),
        },
        "dead_capital_opportunity_cost": dead_capital_opportunity_cost,
        "synthetic_assets": synthetic_state,
        "synthetic_metrics": synthetic_metrics,
        "gcp_billing": gcp_billing,
        "updated_at": state.get("updated_at"),
    }
    return normalized, state_changed


def mutate_state(mutator: Callable[[dict], Any]) -> tuple[dict, Any]:
    """Atomically apply ``mutator`` to the normalized wallet state.

    The mutator mutates the state dict in place and may return a result.
    New transactions it inserts are mirrored to the append-only ledger, and
    the wallet document keeps only the most recent entries.
    Returns ``(persisted_state, mutator_result)``.
    """

    def callback(txn):
        doc = wallet_doc()
        snapshot = doc.get(transaction=txn)
        raw = snapshot.to_dict() if snapshot.exists else {}
        state, _ = normalize_state(raw)
        before_ids = {t.get("id") for t in state["transactions"]}

        result = mutator(state)

        state, _ = normalize_state(state)
        new_entries = [t for t in state["transactions"] if t.get("id") not in before_ids]
        state["transactions"] = state["transactions"][: config.MAX_WALLET_TRANSACTIONS]
        state["updated_at"] = datetime.now(IST).isoformat()

        txn.set(doc, state)
        ledger = ledger_collection()
        for entry in new_entries:
            txn.set(ledger.document(entry["id"]), entry)
        return state, result

    return run_transaction(callback)


def load_state() -> dict:
    """Read the normalized wallet state. If normalization changed anything
    (e.g. daily interest accrual), persist it through the transactional path."""
    snapshot = wallet_doc().get()
    raw = snapshot.to_dict() if snapshot.exists else {}
    state, changed = normalize_state(raw)
    if changed:
        state, _ = mutate_state(lambda _s: None)
    return state


def get_gmail_sync_state() -> dict:
    doc = gmail_sync_doc().get()
    if doc.exists:
        return {**DEFAULT_SYNC_STATE, **doc.to_dict()}
    return dict(DEFAULT_SYNC_STATE)


def save_gmail_sync_state(sync_state: dict):
    gmail_sync_doc().set(sync_state)


def merge_gmail_sync_state(history_id, newly_processed_ids: list[str], synced_count: int) -> dict:
    """Atomically merge webhook results into the sync cursor so concurrent
    Pub/Sub deliveries cannot drop each other's dedup entries."""

    def callback(txn):
        doc = gmail_sync_doc()
        snapshot = doc.get(transaction=txn)
        sync_state = {**DEFAULT_SYNC_STATE, **(snapshot.to_dict() if snapshot.exists else {})}
        processed = list(sync_state.get("processed_message_ids") or [])
        for message_id in newly_processed_ids:
            if message_id not in processed:
                processed.append(message_id)
        sync_state["processed_message_ids"] = processed[-config.MAX_PROCESSED_MESSAGE_IDS:]
        sync_state["last_history_id"] = history_id
        sync_state["last_sync_at"] = datetime.now(IST).isoformat()
        sync_state["total_synced"] = int(sync_state.get("total_synced", 0) or 0) + synced_count
        txn.set(doc, sync_state)
        return sync_state

    return run_transaction(callback)
