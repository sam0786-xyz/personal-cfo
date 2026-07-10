"""Pipeline 2: ground truth from bank emails.

Gmail push notification (Pub/Sub) → history diff → per-message security
gates → LLM extraction → transactional balance reconciliation. Raw email
bodies are never logged and never persisted; only the structured
transaction survives.
"""

import base64
import logging

from bs4 import BeautifulSoup
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app import config, llm, security, state
from app.classification import classify_transaction
from app.normalization import (
    extract_upi_vpa,
    normalize_payee,
    transaction_identity_key,
    upi_handle_from_vpa,
)

logger = logging.getLogger("personal-cfo")


def _get_secret(secret_id: str) -> str:
    sm_client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{config.GCP_PROJECT}/secrets/{secret_id}/versions/latest"
    response = sm_client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


def get_gmail_service():
    """Build an authenticated Gmail API service from Secret Manager creds."""
    refresh_token = _get_secret("gmail-refresh-token")
    client_id = _get_secret("gmail-client-id")
    client_secret = _get_secret("gmail-client-secret")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def strip_html(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def extract_email_body(payload: dict) -> str:
    """Extract a plain-text body from a Gmail message payload, preferring
    text/plain, falling back to stripped text/html, recursing into
    multipart containers."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            return strip_html(html)

    for part in parts:
        if "parts" in part:
            result = extract_email_body(part)
            if result:
                return result

    return ""


def enrich_transaction_identity(txn_data: dict, source_text: str = "") -> dict:
    merchant = txn_data.get("merchant", "") or txn_data.get("payee_raw", "")
    payee_raw = txn_data.get("payee_raw") or merchant
    upi_vpa = (txn_data.get("upi_vpa") or extract_upi_vpa(source_text, merchant, payee_raw)).lower()
    payee_normalized = txn_data.get("payee_normalized") or normalize_payee(payee_raw or merchant or upi_vpa)
    return {
        **txn_data,
        "merchant": merchant or payee_raw or upi_vpa or "Unknown",
        "payee_raw": payee_raw or merchant or upi_vpa or "Unknown",
        "payee_normalized": payee_normalized,
        "merchant_normalized": normalize_payee(merchant or payee_raw or upi_vpa),
        "upi_vpa": upi_vpa,
        "upi_handle": txn_data.get("upi_handle") or upi_handle_from_vpa(upi_vpa),
        "direction": txn_data.get("direction") or ("INBOUND" if txn_data.get("type") == "CREDIT" else "OUTBOUND"),
    }


def apply_bank_transaction(state_dict: dict, txn_data: dict) -> dict | None:
    """Apply a parsed bank transaction to the wallet state (in place).
    Returns the created transaction record, or None if the amount is invalid.
    Pure with respect to I/O so it can run inside a Firestore transaction."""
    from datetime import datetime

    from app.config import IST

    current_balance = state_dict.get("current_balance", config.DEFAULT_BALANCE)
    amount = int(txn_data.get("amount", 0))
    txn_type = txn_data.get("type", "DEBIT")

    if amount <= 0:
        return None

    if txn_type == "DEBIT":
        new_balance = current_balance - amount
        action = "BANK_DEBIT"
    else:
        new_balance = current_balance + amount
        action = "BANK_CREDIT"

    state_dict["current_balance"] = new_balance

    # Sanitized log entry only — never the raw email body.
    transaction = {
        "timestamp": datetime.now(IST).isoformat(),
        "expense_text": f"🏦 Auto-Synced: {txn_type} ₹{amount} via {txn_data.get('channel', 'UNKNOWN')}",
        "action_taken": action,
        "approved_amount": amount if txn_type == "DEBIT" else 0,
        "funds_added": amount if txn_type == "CREDIT" else 0,
        "message": f"{txn_data.get('merchant', 'Unknown')} — Ref: {txn_data.get('txn_ref', 'N/A')}",
        "remaining_balance": new_balance,
        "owed_by_snapshot": dict(state_dict.get("owed_by", {})),
        "source": "GMAIL_SYNC",
        "channel": txn_data.get("channel", "UNKNOWN"),
        "merchant": txn_data.get("merchant", "Unknown"),
        "merchant_normalized": txn_data.get("merchant_normalized", ""),
        "payee_raw": txn_data.get("payee_raw", ""),
        "payee_normalized": txn_data.get("payee_normalized", ""),
        "upi_vpa": txn_data.get("upi_vpa", ""),
        "upi_handle": txn_data.get("upi_handle", ""),
        "direction": txn_data.get("direction", "OUTBOUND"),
        "txn_ref": txn_data.get("txn_ref", ""),
    }
    transaction["id"] = transaction_identity_key(transaction)
    transaction = classify_transaction(transaction, state_dict)
    state_dict["transactions"].insert(0, transaction)
    return transaction


def reconcile_bank_transaction(txn_data: dict, source_text: str = ""):
    """Enrich and apply a parsed bank transaction atomically."""
    enriched = enrich_transaction_identity(txn_data, source_text)
    return state.mutate_state(lambda s: apply_bank_transaction(s, enriched))


def process_history_update(history_id) -> dict:
    """Process a Gmail history notification: fetch new messages, run them
    through the security gates, and reconcile any bank transactions."""
    sync_state = state.get_gmail_sync_state()
    last_history_id = sync_state.get("last_history_id")
    processed_ids = set(sync_state.get("processed_message_ids") or [])

    if not last_history_id:
        # First run — anchor the cursor and return.
        state.merge_gmail_sync_state(history_id, [], 0)
        return {"status": "initialized", "history_id": history_id}

    service = get_gmail_service()
    history_response = (
        service.users()
        .history()
        .list(userId="me", startHistoryId=last_history_id, historyTypes=["messageAdded"])
        .execute()
    )
    history_list = history_response.get("history", [])

    # Gmail label filters are treated as best-effort (Layer 1); the
    # deterministic gates below are what actually guarantee OTP safety.
    newly_processed: list[str] = []
    processed_count = 0

    for history_item in history_list:
        for msg_added in history_item.get("messagesAdded", []):
            msg_id = msg_added["message"]["id"]

            if msg_id in processed_ids or msg_id in newly_processed:
                continue

            try:
                # Headers only — the body is fetched only after the subject gate.
                msg_meta = (
                    service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["From", "Subject"])
                    .execute()
                )
            except Exception as fetch_err:
                logger.warning(f"Skipping message {msg_id}: {fetch_err}")
                newly_processed.append(msg_id)
                continue

            headers = {h["name"].lower(): h["value"] for h in msg_meta.get("payload", {}).get("headers", [])}
            sender = headers.get("from", "")
            subject = headers.get("subject", "")

            if config.BANK_ALERT_SENDER not in sender.lower():
                newly_processed.append(msg_id)
                continue

            # Layer 2: subject-line gate.
            if not security.is_safe_transaction_email(subject):
                logger.info(f"Layer 2 blocked: {subject[:50]}...")
                newly_processed.append(msg_id)
                continue

            try:
                full_msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            except Exception as body_err:
                logger.warning(f"Skipping body fetch for {msg_id}: {body_err}")
                newly_processed.append(msg_id)
                continue

            body_text = extract_email_body(full_msg.get("payload", {}))
            if not body_text:
                newly_processed.append(msg_id)
                continue

            # Layer 3: redact OTP-shaped content before the LLM sees anything.
            safe_body_text = security.sanitize_body(body_text)

            txn_data = llm.parse_bank_email(safe_body_text)
            if txn_data:
                _, created = reconcile_bank_transaction(txn_data, safe_body_text)
                if created:
                    processed_count += 1
                    logger.info(
                        f"Bank sync: {txn_data.get('type')} ₹{txn_data.get('amount')} "
                        f"via {txn_data.get('channel')} — {txn_data.get('merchant')}"
                    )

            newly_processed.append(msg_id)

    state.merge_gmail_sync_state(history_id, newly_processed, processed_count)
    return {"status": "success", "processed": processed_count}
