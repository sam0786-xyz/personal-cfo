"""Payee/transaction identity normalization shared by state and classification."""

import hashlib
import re

UPI_VPA_RE = re.compile(r"\b[a-zA-Z0-9._-]{2,256}@[a-zA-Z][a-zA-Z0-9._-]{1,64}\b")


def normalize_payee(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"\b(upi|payment|paid|to|from|ref|rrn|transaction|a/c|account|axis|bank)\b", " ", value)
    value = re.sub(r"[^a-z0-9@._ -]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120]


def extract_upi_vpa(*values: str) -> str:
    for value in values:
        match = UPI_VPA_RE.search(value or "")
        if match:
            return match.group(0).lower()
    return ""


def upi_handle_from_vpa(upi_vpa: str) -> str:
    if "@" not in (upi_vpa or ""):
        return ""
    return "@" + upi_vpa.split("@", 1)[1].lower()


def transaction_identity_key(transaction: dict) -> str:
    seed = "|".join([
        str(transaction.get("timestamp", "")),
        str(transaction.get("txn_ref", "")),
        str(transaction.get("approved_amount", transaction.get("funds_added", ""))),
        str(transaction.get("merchant_normalized", transaction.get("merchant", ""))),
        str(transaction.get("action_taken", "")),
        str(transaction.get("fallback_index", "")),
    ])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def normalize_transaction_record(transaction: dict, index: int = 0) -> tuple[dict, bool]:
    transaction = transaction or {}
    changed = False
    if not transaction.get("id"):
        transaction["id"] = transaction_identity_key({**transaction, "fallback_index": index})
        changed = True

    merchant = transaction.get("merchant") or transaction.get("payee_raw") or ""
    if not merchant and transaction.get("message"):
        merchant = str(transaction.get("message", "")).split("—", 1)[0].strip()
    upi_vpa = extract_upi_vpa(
        transaction.get("upi_vpa", ""),
        transaction.get("payee_raw", ""),
        transaction.get("merchant", ""),
        transaction.get("message", ""),
    )
    payee_raw = transaction.get("payee_raw") or merchant
    merchant_normalized = transaction.get("merchant_normalized") or normalize_payee(payee_raw)

    defaults = {
        "merchant": merchant,
        "payee_raw": payee_raw,
        "payee_normalized": transaction.get("payee_normalized") or merchant_normalized,
        "merchant_normalized": merchant_normalized,
        "upi_vpa": upi_vpa,
        "upi_handle": transaction.get("upi_handle") or upi_handle_from_vpa(upi_vpa),
        "direction": transaction.get("direction") or ("INBOUND" if transaction.get("funds_added", 0) else "OUTBOUND"),
        "category": transaction.get("category") or "Unknown",
        "subcategory": transaction.get("subcategory") or "",
        "classification_confidence": float(transaction.get("classification_confidence", 0.0) or 0.0),
        "classification_source": transaction.get("classification_source") or "legacy",
        "classification_reason": transaction.get("classification_reason") or "Legacy transaction; review if needed.",
        "needs_review": bool(transaction.get("needs_review", transaction.get("category") in [None, "", "Unknown"])),
    }
    for key, value in defaults.items():
        if transaction.get(key) != value:
            transaction[key] = value
            changed = True
    return transaction, changed


def get_transaction_amount(transaction: dict) -> int:
    return int(transaction.get("approved_amount") or transaction.get("funds_added") or transaction.get("amount") or 0)
