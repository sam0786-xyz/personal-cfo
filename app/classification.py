"""Transaction classification cascade.

Order matters: learned rules (user corrections) beat keyword heuristics,
which beat recurring-pattern inference, which beats the LLM fallback.
Anything still unresolved is queued for human review, and each human
correction becomes a learned rule that retroactively reclassifies.
"""

import hashlib
from datetime import datetime

from fastapi import HTTPException

from app import llm
from app.config import IST
from app.normalization import (
    get_transaction_amount,
    normalize_payee,
    normalize_transaction_record,
)

CATEGORIES = {
    "Food",
    "Transport",
    "Shopping",
    "Rent/Housing",
    "Utilities",
    "Subscriptions",
    "Business/Tools",
    "Travel",
    "Healthcare",
    "Education",
    "Income",
    "Transfer",
    "Refund",
    "Fees/Charges",
    "Unknown",
}

SYSTEM_MERCHANT_RULES = [
    ("Food", "Cafe/Restaurant", ["uber eats", "restaurant", "cafe", "coffee", "starbucks", "tea", "chai", "snack", "food", "zomato", "swiggy", "hotel", "dhaba", "bakery", "juice", "biryani", "pizza", "kitchen", "grocery", "mart"]),
    ("Transport", "Mobility", ["metro", "uber", "ola", "rapido", "namma yatri", "auto", "cab", "taxi", "fuel", "petrol", "diesel", "parking", "toll", "irctc", "bus"]),
    ("Shopping", "Retail", ["amazon", "flipkart", "myntra", "nykaa", "mall", "store", "retail", "fashion", "electronics", "croma", "reliance digital"]),
    ("Business/Tools", "Software", ["google cloud", "gcp", "github", "openai", "anthropic", "vercel", "render", "aws", "digitalocean", "cursor", "notion", "slack", "api"]),
    ("Subscriptions", "Recurring", ["netflix", "spotify", "youtube", "prime", "subscription", "autopay", "mandate", "recurring"]),
    ("Utilities", "Bills", ["electricity", "water", "broadband", "wifi", "airtel", "jio", "vi ", "bsnl", "gas", "bill"]),
    ("Travel", "Travel", ["flight", "airlines", "indigo", "air india", "vistara", "akasa", "hotel booking", "makemytrip", "goibibo", "booking.com"]),
    ("Healthcare", "Medical", ["pharmacy", "apollo", "medical", "hospital", "clinic", "doctor", "diagnostic", "pathlab"]),
    ("Education", "Learning", ["course", "udemy", "coursera", "edx", "college", "university", "book", "exam"]),
    ("Fees/Charges", "Bank Fees", ["charges", "fee", "penalty", "interest", "gst", "annual fee"]),
]

# Merchants whose name alone is ambiguous (e.g. "uber" could be a ride or
# Uber Eats). When conflicting hints co-occur, skip keyword matching and let
# a later stage (or the user) decide.
AMBIGUOUS_MERCHANT_HINTS = {
    "uber": ["eats", "food", "restaurant", "cafe", "grocery", "kitchen"],
    "hotel": ["restaurant", "stay", "room", "booking"],
    "mart": ["grocery", "fashion", "electronics", "medical"],
}


def validate_category(category: str) -> str:
    category = (category or "").strip()
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unsupported category: {category}")
    return category


def sanitize_rule(rule: dict) -> dict | None:
    pattern = normalize_payee(rule.get("pattern") or rule.get("payee") or "")
    category = rule.get("category", "Unknown")
    if not pattern or category not in CATEGORIES:
        return None
    now = datetime.now(IST).isoformat()
    return {
        "id": rule.get("id") or hashlib.sha256(pattern.encode("utf-8")).hexdigest()[:16],
        "pattern": pattern,
        "match_type": rule.get("match_type") or ("upi_vpa" if "@" in pattern else "payee"),
        "category": category,
        "subcategory": rule.get("subcategory", ""),
        "confidence": float(rule.get("confidence", 0.98)),
        "source": rule.get("source", "learned"),
        "created_at": rule.get("created_at") or now,
        "last_used_at": rule.get("last_used_at"),
        "usage_count": int(rule.get("usage_count", 0) or 0),
    }


def normalize_merchant_rules(rules: list[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for rule in rules or []:
        clean_rule = sanitize_rule(rule)
        if clean_rule and clean_rule["pattern"] not in seen:
            seen.add(clean_rule["pattern"])
            normalized.append(clean_rule)
    return normalized


def apply_learned_rule(transaction: dict, merchant_rules: list[dict]) -> dict | None:
    candidates = {
        normalize_payee(transaction.get("upi_vpa", "")),
        normalize_payee(transaction.get("payee_normalized", "")),
        normalize_payee(transaction.get("merchant_normalized", "")),
    }
    candidates.discard("")
    for rule in merchant_rules:
        if normalize_payee(rule.get("pattern", "")) in candidates:
            return {
                "category": rule["category"],
                "subcategory": rule.get("subcategory", ""),
                "classification_confidence": float(rule.get("confidence", 0.98)),
                "classification_source": rule.get("source", "learned"),
                "classification_reason": f"Matched learned rule: {rule['pattern']}",
                "needs_review": False,
                "_matched_rule_id": rule.get("id"),
            }
    return None


def apply_keyword_rule(transaction: dict) -> dict | None:
    haystack = " ".join([
        transaction.get("merchant", ""),
        transaction.get("payee_raw", ""),
        transaction.get("payee_normalized", ""),
        transaction.get("merchant_normalized", ""),
        transaction.get("upi_vpa", ""),
    ]).lower()
    for merchant_hint, conflict_hints in AMBIGUOUS_MERCHANT_HINTS.items():
        if merchant_hint in haystack and any(hint in haystack for hint in conflict_hints):
            return None
    for category, subcategory, keywords in SYSTEM_MERCHANT_RULES:
        if any(keyword in haystack for keyword in keywords):
            return {
                "category": category,
                "subcategory": subcategory,
                "classification_confidence": 0.86,
                "classification_source": "system",
                "classification_reason": "Matched known merchant keyword.",
                "needs_review": False,
            }
    return None


def apply_recurring_pattern(transaction: dict, transactions: list[dict]) -> dict | None:
    payee = normalize_payee(transaction.get("merchant_normalized") or transaction.get("payee_normalized") or transaction.get("upi_vpa"))
    amount = get_transaction_amount(transaction)
    if not payee or amount <= 0:
        return None
    matches: dict[str, int] = {}
    for existing in transactions or []:
        if existing.get("id") == transaction.get("id"):
            continue
        existing_payee = normalize_payee(existing.get("merchant_normalized") or existing.get("payee_normalized") or existing.get("upi_vpa"))
        existing_amount = get_transaction_amount(existing)
        category = existing.get("category")
        if existing_payee == payee and category and category != "Unknown" and abs(existing_amount - amount) <= max(5, amount * 0.03):
            matches[category] = matches.get(category, 0) + 1
    if not matches:
        return None
    category = max(matches, key=matches.get)
    if matches[category] < 2:
        return None
    return {
        "category": category,
        "subcategory": "Recurring pattern",
        "classification_confidence": 0.74,
        "classification_source": "recurring",
        "classification_reason": "Matched repeated payee and amount pattern.",
        "needs_review": False,
    }


def classify_with_llm(transaction: dict) -> dict | None:
    sanitized_payload = {
        "type": transaction.get("direction"),
        "channel": transaction.get("channel"),
        "amount": get_transaction_amount(transaction),
        "merchant": transaction.get("merchant"),
        "payee_raw": transaction.get("payee_raw"),
        "payee_normalized": transaction.get("payee_normalized"),
        "upi_vpa": transaction.get("upi_vpa"),
        "upi_handle": transaction.get("upi_handle"),
        "classification_context": "Minimal context only. If merchant text contains conflicting signals such as Uber plus food/eats, resolve from the text if confident.",
    }
    parsed = llm.classify_structured_transaction(sanitized_payload, CATEGORIES)
    if not parsed:
        return None
    category = parsed.get("category", "Unknown")
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    if category not in CATEGORIES or confidence < 0.78:
        return None
    return {
        "category": category,
        "subcategory": parsed.get("subcategory", ""),
        "classification_confidence": min(confidence, 0.89),
        "classification_source": "llm",
        "classification_reason": parsed.get("reason", "LLM classified sanitized structured fields."),
        "needs_review": False,
    }


def classify_transaction(transaction: dict, state: dict) -> dict:
    transaction, _ = normalize_transaction_record(transaction)
    txn_type = transaction.get("direction") or ("INBOUND" if transaction.get("funds_added", 0) else "OUTBOUND")
    if transaction.get("action_taken") == "BANK_CREDIT" or txn_type == "INBOUND":
        merchant_text = " ".join([transaction.get("merchant", ""), transaction.get("message", "")]).lower()
        is_refund = any(word in merchant_text for word in ["refund", "reversal", "reversed", "cashback"])
        return {
            **transaction,
            "category": "Refund" if is_refund else "Income",
            "subcategory": "Refund" if is_refund else "Bank credit",
            "classification_confidence": 0.82,
            "classification_source": "system",
            "classification_reason": "Credit transactions are excluded from spend categories.",
            "needs_review": False,
        }

    for classifier in [
        lambda tx: apply_learned_rule(tx, state.get("merchant_rules", [])),
        apply_keyword_rule,
        lambda tx: apply_recurring_pattern(tx, state.get("transactions", [])),
        classify_with_llm,
    ]:
        result = classifier(transaction)
        if result:
            matched_rule_id = result.pop("_matched_rule_id", None)
            if matched_rule_id:
                bump_rule_usage(state, matched_rule_id)
            return {**transaction, **result}

    return {
        **transaction,
        "category": "Unknown",
        "subcategory": "",
        "classification_confidence": 0.0,
        "classification_source": "review",
        "classification_reason": "Ambiguous UPI/vendor data; waiting for your correction.",
        "needs_review": True,
    }


def bump_rule_usage(state: dict, rule_id: str):
    now = datetime.now(IST).isoformat()
    for rule in state.get("merchant_rules", []):
        if rule.get("id") == rule_id:
            rule["usage_count"] = int(rule.get("usage_count", 0) or 0) + 1
            rule["last_used_at"] = now
            break


def upsert_merchant_rule(state: dict, rule: dict):
    now = datetime.now(IST).isoformat()
    rules = state.setdefault("merchant_rules", [])
    pattern = normalize_payee(rule.get("pattern", ""))
    for existing in rules:
        if normalize_payee(existing.get("pattern", "")) == pattern:
            existing.update({
                "category": rule["category"],
                "subcategory": rule.get("subcategory", ""),
                "confidence": rule.get("confidence", 0.98),
                "source": rule.get("source", existing.get("source", "learned")),
                "match_type": rule.get("match_type", existing.get("match_type", "payee")),
                "last_used_at": now,
                "usage_count": int(existing.get("usage_count", 0) or 0) + 1,
            })
            return existing
    rule["last_used_at"] = now
    rule["usage_count"] = int(rule.get("usage_count", 0) or 0) + 1
    rules.append(rule)
    return rule


def transaction_matches_rule(transaction: dict, rule: dict) -> bool:
    pattern = normalize_payee(rule.get("pattern", ""))
    candidates = {
        normalize_payee(transaction.get("upi_vpa", "")),
        normalize_payee(transaction.get("payee_normalized", "")),
        normalize_payee(transaction.get("merchant_normalized", "")),
    }
    candidates.discard("")
    return bool(pattern and pattern in candidates)


def reclassify_similar_transactions(state: dict, rule: dict, skip_id: str = "") -> int:
    count = 0
    for transaction in state.get("transactions", []):
        if skip_id and transaction.get("id") == skip_id:
            continue
        if not transaction.get("needs_review"):
            continue
        if not transaction_matches_rule(transaction, rule):
            continue
        transaction["category"] = rule["category"]
        transaction["subcategory"] = rule.get("subcategory", "")
        transaction["classification_confidence"] = float(rule.get("confidence", 0.98))
        transaction["classification_source"] = rule.get("source", "learned")
        transaction["classification_reason"] = f"Reclassified from learned rule: {rule['pattern']}"
        transaction["needs_review"] = False
        count += 1
    return count
