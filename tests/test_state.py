"""Tests for transactional state mutation, the ledger, and normalization."""

from datetime import datetime, timedelta

from app import config, state
from app.config import IST


def seed_wallet(fake_db, **overrides):
    doc = {"current_balance": 5000, "transactions": [], "owed_by": {}}
    doc.update(overrides)
    fake_db.store["cfo_state/wallet"] = doc
    return doc


class TestMutateState:
    def test_mutation_persists_and_returns_result(self, fake_db):
        seed_wallet(fake_db, current_balance=5000)

        def spend(s):
            s["current_balance"] -= 300
            return "done"

        persisted, result = state.mutate_state(spend)
        assert result == "done"
        assert persisted["current_balance"] == 4700
        assert fake_db.store["cfo_state/wallet"]["current_balance"] == 4700

    def test_new_transactions_are_mirrored_to_ledger(self, fake_db):
        seed_wallet(fake_db)

        def add_txn(s):
            s["transactions"].insert(0, {
                "timestamp": datetime.now(IST).isoformat(),
                "action_taken": "BANK_DEBIT",
                "approved_amount": 340,
                "merchant": "SWIGGY",
                "message": "test",
                "remaining_balance": 4660,
            })

        persisted, _ = state.mutate_state(add_txn)
        ledger = fake_db.docs_in("cfo_ledger")
        assert len(ledger) == 1
        entry = next(iter(ledger.values()))
        assert entry["approved_amount"] == 340
        assert entry["id"] == persisted["transactions"][0]["id"]

    def test_wallet_doc_is_capped_but_ledger_keeps_everything(self, fake_db, monkeypatch):
        monkeypatch.setattr(config, "MAX_WALLET_TRANSACTIONS", 5)
        seed_wallet(fake_db)

        for i in range(8):
            def add(s, i=i):
                s["transactions"].insert(0, {
                    "timestamp": f"2026-07-0{(i % 9) + 1}T10:00:00+05:30",
                    "action_taken": "BANK_DEBIT",
                    "approved_amount": 100 + i,
                    "merchant": f"SHOP-{i}",
                    "remaining_balance": 5000 - i,
                })
            state.mutate_state(add)

        wallet = fake_db.store["cfo_state/wallet"]
        assert len(wallet["transactions"]) == 5
        assert len(fake_db.docs_in("cfo_ledger")) == 8

    def test_mutation_reads_fresh_state_not_caller_snapshot(self, fake_db):
        """The mutator operates on the state read inside the transaction, so
        a balance changed between prompt-time and commit-time is respected."""
        seed_wallet(fake_db, current_balance=5000)
        stale_view = state.load_state()
        assert stale_view["current_balance"] == 5000

        # A bank debit lands "concurrently".
        fake_db.store["cfo_state/wallet"]["current_balance"] = 1000

        def spend(s):
            s["current_balance"] -= 300

        persisted, _ = state.mutate_state(spend)
        assert persisted["current_balance"] == 700  # 1000 - 300, not 5000 - 300


class TestGmailSyncState:
    def test_merge_deduplicates_and_caps_window(self, fake_db, monkeypatch):
        monkeypatch.setattr(config, "MAX_PROCESSED_MESSAGE_IDS", 4)
        state.merge_gmail_sync_state("h1", ["m1", "m2"], 1)
        state.merge_gmail_sync_state("h2", ["m2", "m3", "m4", "m5"], 2)

        sync = state.get_gmail_sync_state()
        assert sync["last_history_id"] == "h2"
        assert sync["total_synced"] == 3
        assert sync["processed_message_ids"] == ["m2", "m3", "m4", "m5"]

    def test_default_sync_state(self, fake_db):
        sync = state.get_gmail_sync_state()
        assert sync["last_history_id"] is None
        assert sync["total_synced"] == 0


class TestNormalizeState:
    def test_empty_state_gets_safe_defaults(self, fake_db):
        normalized, _ = state.normalize_state({})
        assert normalized["current_balance"] == config.DEFAULT_BALANCE
        assert normalized["transactions"] == []
        assert normalized["liquid_assets"]["principal"] == 0.0
        assert normalized["synthetic_metrics"]["total_cash_equivalent"] == 0
        assert normalized["gcp_billing"]["vertex_runway_days"] == -1

    def test_daily_interest_accrual(self, fake_db):
        ten_days_ago = (datetime.now(IST).date() - timedelta(days=10)).isoformat()
        raw = {
            "liquid_assets": {
                "principal": 10000.0,
                "stamp_duty_paid": True,
                "last_accrual_date": ten_days_ago,
            }
        }
        normalized, changed = state.normalize_state(raw)
        expected = 10000.0 * (1 + config.LIQUID_APY / 365.0) ** 10
        assert changed is True
        assert abs(normalized["liquid_assets"]["principal"] - expected) < 0.01
        assert normalized["liquid_metrics"]["spendable"] == round(expected * 0.9, 2)

    def test_owed_by_drops_settled_debts(self, fake_db):
        normalized, _ = state.normalize_state({"owed_by": {"faizan": 500, "settled": 0, " ": 100}})
        assert normalized["owed_by"] == {"Faizan": 500}

    def test_transactions_get_stable_ids(self, fake_db):
        raw = {"transactions": [{"timestamp": "t1", "message": "x"}, {"timestamp": "t2", "message": "y"}]}
        normalized, changed = state.normalize_state(raw)
        ids = [t["id"] for t in normalized["transactions"]]
        assert changed is True
        assert len(set(ids)) == 2
        # Re-normalizing is idempotent.
        renormalized, changed_again = state.normalize_state(normalized)
        assert [t["id"] for t in renormalized["transactions"]] == ids
