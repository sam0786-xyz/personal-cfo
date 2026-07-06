from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import base64
import logging
import re
import hmac
import hashlib
from google.cloud import firestore
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

load_dotenv()
logger = logging.getLogger("personal-cfo")

app = FastAPI(title="Action Button CFO Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cfo.sam18.xyz",
        "https://personal-cfo-525303710200.us-central1.run.app",
        "http://localhost:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Force Safari to revalidate HTML on every load (prevents stale cache)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
IST = ZoneInfo("Asia/Kolkata")

db = firestore.Client()
doc_ref = db.collection("cfo_state").document("wallet")
gmail_sync_ref = db.collection("cfo_state").document("gmail_sync")
DEFAULT_BALANCE = 5000
TARGET_DATE = datetime(2026, 6, 22, tzinfo=IST)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "gen-lang-client-0592771092")
AXIS_BANK_SENDER = "alerts@axis.bank.in"
CFO_ACCESS_TOKEN = os.environ.get("CFO_ACCESS_TOKEN")
CFO_AUTH_DISABLED = os.environ.get("CFO_AUTH_DISABLED", "").lower() in {"1", "true", "yes"}

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
    ("Food", "Cafe/Restaurant", ["restaurant", "cafe", "coffee", "starbucks", "tea", "chai", "snack", "food", "zomato", "swiggy", "hotel", "dhaba", "bakery", "juice", "biryani", "pizza", "kitchen", "grocery", "mart"]),
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

UPI_VPA_RE = re.compile(r"\b[a-zA-Z0-9._-]{2,256}@[a-zA-Z][a-zA-Z0-9._-]{1,64}\b")

def _extract_access_token(request: Request) -> str:
    """Read private dashboard token from standard places."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return (
        request.headers.get("x-cfo-token")
        or request.query_params.get("token")
        or request.cookies.get("cfo_access_token")
        or ""
    ).strip()

def require_private_access(request: Request):
    """Protect personal finance data from public resume visitors."""
    if CFO_AUTH_DISABLED:
        return
    if not CFO_ACCESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Private CFO access is not configured. Set CFO_ACCESS_TOKEN or CFO_AUTH_DISABLED=true for local development.",
        )
    supplied_token = _extract_access_token(request)
    if not supplied_token or not hmac.compare_digest(supplied_token, CFO_ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Private CFO access required.")

# ==========================================================================
#  GMAIL GROUND TRUTH PIPELINE — 3-LAYER OTP DEFENSE
# ==========================================================================

# --- Layer 2: Subject-Line Gate (Deterministic) ---
TRANSACTION_KEYWORDS = [
    "debited", "credited", "transaction alert", "a/c no",
    "withdrawn", "received", "transfer", "payment",
    "purchase", "refund", "reversed", "emi", "auto-debit",
    "neft", "imps", "upi", "standing instruction",
    "amount", "inr"
]

BLOCK_KEYWORDS = [
    "otp", "one time password", "password", "pin",
    "verification", "verify", "login", "authenticate",
    "reset", "security code", "2fa", "do not share",
    "temporary password"
]

def is_safe_transaction_email(subject: str) -> bool:
    """Layer 2: Check subject line. Returns True only for transaction alerts."""
    subject_lower = subject.lower()
    # HARD BLOCK: Any blocked keyword → reject immediately
    if any(kw in subject_lower for kw in BLOCK_KEYWORDS):
        return False
    # ALLOW: Only if a transaction keyword is present
    if any(kw in subject_lower for kw in TRANSACTION_KEYWORDS):
        return True
    # DEFAULT-DENY: Unknown → skip for safety
    return False

# --- Layer 3: Body Sanitizer (Deterministic) ---
def sanitize_body(raw_body: str) -> str:
    """Layer 3: Scan body for OTP terms BEFORE sending to Gemini and redact them.
    Unlike a kill-switch, this allows transaction emails with standard
    bank footers (e.g. 'Never share your OTP') to still be processed."""
    
    # 1. Redact numbers immediately following sensitive keywords
    redacted = re.sub(r'(?i)(otp|pin|password|code)[\s\-:]*(\d{4,8})', r'\1 [BLOCKED]', raw_body)
    
    # 2. Redact standalone 6-digit numbers as an extra safety net
    redacted = re.sub(r'\b\d{6}\b', '[BLOCKED]', redacted)
    
    return redacted

def strip_html(html_content: str) -> str:
    """Strip HTML tags and return plain text."""
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=" ", strip=True)

# --- Gmail Service Builder ---
def _get_secret(secret_id: str) -> str:
    """Fetch a secret from Google Secret Manager."""
    sm_client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT}/secrets/{secret_id}/versions/latest"
    response = sm_client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def get_gmail_service():
    """Build an authenticated Gmail API service using stored OAuth credentials."""
    refresh_token = _get_secret("gmail-refresh-token")
    client_id = _get_secret("gmail-client-id")
    client_secret = _get_secret("gmail-client-secret")
    
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)

# --- Gemini Bank Email Parser ---
class BankTransaction(BaseModel):
    amount: int
    type: str       # DEBIT or CREDIT
    merchant: str
    channel: str    # UPI, NEFT, IMPS, ATM, CARD, AUTO_DEBIT, OTHER
    txn_ref: str
    payee_raw: str = ""
    payee_normalized: str = ""
    upi_vpa: str = ""
    upi_handle: str = ""
    direction: str = ""  # OUTBOUND or INBOUND

def parse_bank_email(body_text: str) -> dict | None:
    """Use Gemini to extract structured transaction data from a bank email."""
    if client is None:
        logger.error("Gemini client not configured.")
        return None
    
    system_prompt = (
        "You are a bank transaction data extractor. Extract EXACTLY the following from this Axis Bank email:\n"
        "- amount: The exact rupee amount as an integer (no decimals, no commas)\n"
        "- type: 'DEBIT' if money was taken out, 'CREDIT' if money was received/added\n"
        "- merchant: The merchant, beneficiary, or sender name\n"
        "- channel: The payment channel — one of: UPI, NEFT, IMPS, ATM, CARD, AUTO_DEBIT, OTHER\n"
        "- txn_ref: The transaction reference number if present, otherwise empty string\n"
        "- payee_raw: For UPI, the raw payee/payer line exactly as present; otherwise merchant\n"
        "- payee_normalized: Lowercase simplified payee name without bank boilerplate\n"
        "- upi_vpa: UPI ID/VPA if present (example: vendor@oksbi), otherwise empty string\n"
        "- upi_handle: UPI handle/provider beginning with @ if present (example: @oksbi), otherwise empty string\n"
        "- direction: OUTBOUND for debits, INBOUND for credits\n\n"
        "Output ONLY the structured JSON. Do NOT hallucinate amounts, VPAs, or payees. Extract only text present in the email."
    )
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=body_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=BankTransaction,
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                ],
            ),
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logger.error(f"Gemini bank email parsing error: {e}")
        return None

def get_gmail_sync_state() -> dict:
    """Get the Gmail sync state from Firestore."""
    doc = gmail_sync_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {
        "last_history_id": None,
        "watch_expiration": None,
        "label_id": None,
        "processed_message_ids": [],
        "last_sync_at": None,
        "total_synced": 0
    }

def save_gmail_sync_state(state: dict):
    """Save the Gmail sync state to Firestore."""
    gmail_sync_ref.set(state)

def days_until_target():
    today = datetime.now(IST)
    return max((TARGET_DATE.date() - today.date()).days, 1)

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

def get_transaction_amount(transaction: dict) -> int:
    return int(transaction.get("approved_amount") or transaction.get("funds_added") or transaction.get("amount") or 0)

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

class TransactionClassification(BaseModel):
    category: str
    subcategory: str = ""
    confidence: float
    reason: str

def classify_with_llm(transaction: dict) -> dict | None:
    if client is None:
        return None
    sanitized_payload = {
        "type": transaction.get("direction"),
        "channel": transaction.get("channel"),
        "amount": get_transaction_amount(transaction),
        "merchant": transaction.get("merchant"),
        "payee_raw": transaction.get("payee_raw"),
        "payee_normalized": transaction.get("payee_normalized"),
        "upi_vpa": transaction.get("upi_vpa"),
        "upi_handle": transaction.get("upi_handle"),
    }
    system_prompt = (
        "Classify this Indian payment transaction. Use only the provided structured fields. "
        "Do not infer from location, calendar, screenshots, or any unavailable context. "
        f"Category must be one of: {', '.join(sorted(CATEGORIES))}. "
        "For generic person-to-person UPI or ambiguous QR payments, choose Unknown or Transfer; never force Food/Shopping."
    )
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=json.dumps(sanitized_payload),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=TransactionClassification,
            ),
        )
        parsed = json.loads(response.text.strip())
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
    except Exception as e:
        logger.warning(f"LLM classification skipped: {e}")
        return None

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

def normalize_state(state):
    state = state or {}
    current_balance = int(state.get("current_balance", DEFAULT_BALANCE))
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
    
    # Fractional Liquidity Engine (The 90% Rule)
    # Default seed: Curie ICICI Prudential - ₹20,000
    liquid_state = state.get("liquid_assets", {
        "principal": 20000.0,
        "stamp_duty_paid": False,
        "last_accrual_date": datetime.now(IST).date().isoformat()
    })
    
    # 1. Apply Stamp Duty exactly once (0.005%)
    if not liquid_state.get("stamp_duty_paid", False):
        liquid_state["principal"] -= (liquid_state["principal"] * 0.00005)
        liquid_state["stamp_duty_paid"] = True
        state_changed = True
        
    # 2. Accrue daily interest (7% APY)
    last_date = datetime.fromisoformat(liquid_state.get("last_accrual_date")).date()
    today = datetime.now(IST).date()
    days_passed = (today - last_date).days
    
    if days_passed > 0:
        daily_rate = 0.07 / 365.0
        liquid_state["principal"] *= ((1 + daily_rate) ** days_passed)
        liquid_state["last_accrual_date"] = today.isoformat()
        state_changed = True
        
    # 3. Calculate Utilization
    nav = liquid_state["principal"]
    spendable = nav * 0.90
    core = nav * 0.10
    
    # Dead Capital Radar (Opportunity Cost)
    dead_capital_opportunity_cost = round((current_balance * 0.07) / 365.0, 2)
    
    # Synthetic Balance Sheet
    synthetic_state = state.get("synthetic_assets", {
        "axis_edge_points": 25000,
        "github_credits_usd": 200000,
    })
    
    synthetic_metrics = {
        "axis_edge_inr": int(synthetic_state.get("axis_edge_points", 0) * 0.20),
        "github_credits_inr": int(synthetic_state.get("github_credits_usd", 0) * 84)
    }
    synthetic_metrics["total_cash_equivalent"] = synthetic_metrics["axis_edge_inr"] + synthetic_metrics["github_credits_inr"]
    
    # API Burn Radar (GCP Billing)
    gcp_billing = state.get("gcp_billing", {
        "vertex_daily_burn_usd": 150.0  # Default daily burn
    })
    vertex_runway_days = -1
    if gcp_billing["vertex_daily_burn_usd"] > 0:
        vertex_runway_days = int(synthetic_state.get("github_credits_usd", 0) / gcp_billing["vertex_daily_burn_usd"])
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
            "core": round(core, 2)
        },
        "dead_capital_opportunity_cost": dead_capital_opportunity_cost,
        "synthetic_assets": synthetic_state,
        "synthetic_metrics": synthetic_metrics,
        "gcp_billing": gcp_billing,
        "updated_at": state.get("updated_at"),
    }
    return normalized, state_changed

def get_state():
    doc = doc_ref.get()
    raw_state = doc.to_dict() if doc.exists else {"current_balance": DEFAULT_BALANCE, "transactions": [], "owed_by": {}}
    normalized, changed = normalize_state(raw_state)
    if changed:
        save_state(normalized)
    return normalized

def save_state(state):
    state["updated_at"] = datetime.now(IST).isoformat()
    # Strip the runtime `_changed` flag if it accidentally got passed
    if "_changed" in state: del state["_changed"]
    doc_ref.set(state)

class ExpenseRequest(BaseModel):
    expense_text: str | None = None
    text: str | None = None
    input: str | None = None
    query: str | None = None

    def prompt_text(self):
        return (self.expense_text or self.text or self.input or self.query or "").strip()

class AgentDecision(BaseModel):
    message: str
    action_taken: str
    expense_deducted: int
    funds_added: int
    new_target_balance: int
    person_name: str

class AuthSessionRequest(BaseModel):
    token: str

class ClassificationCorrection(BaseModel):
    category: str
    subcategory: str = ""
    always_apply: bool = True

class MerchantRuleRequest(BaseModel):
    pattern: str
    category: str
    subcategory: str = ""
    match_type: str = "payee"

@app.get("/public-profile")
async def public_profile():
    return {
        "title": "AgentCFO",
        "tagline": "A private AI finance cockpit that turns messy bank alerts into decisions, runway, and action.",
        "privacy_note": "Live balances, transactions, debts, bank sync status, and personal audit logs are private.",
        "public_activity": [
            {
                "label": "Ground-truth ingestion",
                "detail": "Built a Gmail-to-Firestore pipeline for bank transaction capture with OTP redaction before model parsing.",
            },
            {
                "label": "Decision engine",
                "detail": "Connected Gemini to classify money intents, lending, collections, and spend approvals from natural language.",
            },
            {
                "label": "Runway dashboard",
                "detail": "Built a FastAPI and vanilla JS cockpit for cash runway, debts, liquid reserves, and bank-sync telemetry.",
            },
            {
                "label": "Next build",
                "detail": "Moving from raw audit feed to categorized spending, rewards inventory, obligations, and proactive CFO recommendations.",
            },
        ],
        "stack": ["FastAPI", "Firestore", "Gmail API", "Pub/Sub", "Gemini", "Cloud Run", "Vanilla JS"],
    }

@app.post("/auth/session")
async def auth_session(auth_request: AuthSessionRequest, response: Response):
    if CFO_AUTH_DISABLED:
        response.set_cookie(
            "cfo_access_token",
            auth_request.token,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
        )
        return {"status": "success"}
    if not CFO_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="Private CFO access is not configured.")
    supplied_token = (auth_request.token or "").strip()
    if not supplied_token or not hmac.compare_digest(supplied_token, CFO_ACCESS_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid private access token.")
    response.set_cookie(
        "cfo_access_token",
        supplied_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"status": "success"}

@app.get("/cfo-state")
async def cfo_state(request: Request):
    require_private_access(request)
    return get_state()

@app.get("/transactions/review")
async def transactions_review(request: Request):
    require_private_access(request)
    state = get_state()
    transactions = [
        tx for tx in state.get("transactions", [])
        if tx.get("needs_review") and tx.get("action_taken") == "BANK_DEBIT"
    ]
    return {"transactions": transactions}

@app.get("/merchant-rules")
async def merchant_rules(request: Request):
    require_private_access(request)
    state = get_state()
    return {"merchant_rules": state.get("merchant_rules", [])}

@app.post("/merchant-rules")
async def create_merchant_rule(rule_request: MerchantRuleRequest, request: Request):
    require_private_access(request)
    category = validate_category(rule_request.category)
    state = get_state()
    rule = sanitize_rule({
        "pattern": rule_request.pattern,
        "match_type": rule_request.match_type,
        "category": category,
        "subcategory": rule_request.subcategory,
        "source": "manual",
        "confidence": 0.98,
    })
    if not rule:
        raise HTTPException(status_code=400, detail="Rule needs a valid pattern and category.")
    upsert_merchant_rule(state, rule)
    reclassified = reclassify_similar_transactions(state, rule)
    save_state(state)
    return {"status": "success", "merchant_rule": rule, "reclassified": reclassified}

@app.post("/transactions/{transaction_id}/classify")
async def classify_existing_transaction(transaction_id: str, correction: ClassificationCorrection, request: Request):
    require_private_access(request)
    category = validate_category(correction.category)
    state = get_state()
    transactions = state.get("transactions", [])
    target = None
    for tx in transactions:
        if tx.get("id") == transaction_id:
            target = tx
            break
    if not target:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    target["category"] = category
    target["subcategory"] = correction.subcategory
    target["classification_confidence"] = 1.0
    target["classification_source"] = "manual"
    target["classification_reason"] = "Corrected in private review workflow."
    target["needs_review"] = False

    rule = None
    reclassified = 0
    if correction.always_apply:
        pattern = target.get("upi_vpa") or target.get("payee_normalized") or target.get("merchant_normalized") or target.get("merchant")
        rule = sanitize_rule({
            "pattern": pattern,
            "match_type": "upi_vpa" if target.get("upi_vpa") else "payee",
            "category": category,
            "subcategory": correction.subcategory,
            "source": "learned",
            "confidence": 0.98,
        })
        if rule:
            upsert_merchant_rule(state, rule)
            reclassified = reclassify_similar_transactions(state, rule, skip_id=transaction_id)

    save_state(state)
    return {"status": "success", "transaction": target, "merchant_rule": rule, "reclassified": reclassified}

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "personal-cfo"}

@app.post("/cfo-reset")
async def cfo_reset(request: Request):
    require_private_access(request)
    state = get_state()
    new_state = {"current_balance": DEFAULT_BALANCE, "transactions": [], "owed_by": {}}
    if "liquid_assets" in state:
        new_state["liquid_assets"] = state["liquid_assets"]
    save_state(new_state)
    return {"status": "success", "message": "Runway reset to ₹5,000.", "state": new_state}

class BillingAlert(BaseModel):
    daily_burn_usd: float

@app.post("/gcp-billing-webhook")
async def gcp_billing_webhook(alert: BillingAlert, request: Request):
    require_private_access(request)
    state = get_state()
    billing = state.get("gcp_billing", {})
    billing["vertex_daily_burn_usd"] = alert.daily_burn_usd
    state["gcp_billing"] = billing
    save_state(state)
    return {"status": "success", "new_burn": alert.daily_burn_usd}

@app.post("/cfo-check")
async def cfo_check(expense_request: ExpenseRequest, request: Request):
    require_private_access(request)
    prompt_text = expense_request.prompt_text()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Send JSON with expense_text, text, input, or query.")
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    raw_state = doc_ref.get().to_dict() if doc_ref.get().exists else {}
    state, _ = normalize_state(raw_state)
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
    owed_by = state.get("owed_by", {})
    
    # Clean up any zero-balance debts from previous runs
    owed_by = {k: v for k, v in owed_by.items() if v > 0}
    
    days_left = days_until_target()
    
    debt_string = ", ".join([f"{name} owes ₹{amt}" for name, amt in owed_by.items()])
    if not debt_string: debt_string = "Nobody owes him money."
    
    synthetic_str = ", ".join([f"{k}: {v}" for k, v in state.get("synthetic_assets", {}).items()])
    if not synthetic_str: synthetic_str = "None"
    
    # THE BULLETPROOF AGENT PROMPT
    system_instruction = (
        f"You are a lightning-fast autonomous financial agent for a 22-year-old AI engineer.\n"
        f"CURRENT STATE:\n- Net worth until June 22nd ({days_left} days left): ₹{current_balance}\n"
        f"- Debts (Money owed to him): {debt_string}\n"
        f"- Synthetic Assets (Points/Credits): {synthetic_str}\n\n"
        f"CRITICAL RULE: DO NOT DO ANY MATH. DO NOT ADD OR SUBTRACT. Only extract the EXACT raw numbers stated by the user.\n"
        f"YOUR CAPABILITIES:\n"
        f"1. ADD_FUNDS: If he explicitly receives extra money, extract the exact amount into 'funds_added'.\n"
        f"2. SET_EXACT_BALANCE: ONLY if he explicitly states his total balance 'became' or 'is now' a specific amount, put that amount in 'new_target_balance'.\n"
        f"3. RETROACTIVE_DEDUCTION: If he ALREADY spent money, extract the amount into 'expense_deducted' and roast him.\n"
        f"4. REJECT_INTENT: If he ASKS to spend on something stupid, set expense_deducted to 0 and reject it.\n"
        f"5. APPROVE_INTENT: If he ASKS to spend on goods/services for himself, extract amount into 'expense_deducted'.\n"
        f"6. LEND_MONEY: CRITICAL - If the transaction involves giving/lending money TO A SPECIFIC PERSON (e.g., 'Faizan', 'my brother'), you MUST choose LEND_MONEY, not Approve Intent. Extract amount into 'expense_deducted' and their name into 'person_name'.\n"
        f"7. DEBT_COLLECTED: CRITICAL - If a person returns/pays back money to him, choose DEBT_COLLECTED. Extract amount into 'funds_added' and their name into 'person_name'.\n"
        f"8. QUERY_STATUS: If he asks for his balance or debts, output 0 for all amounts.\n"
        f"9. MULTI-CURRENCY: If the expense is in a foreign currency (USD, AED, EUR), convert it to INR using rough math (1 USD=84 INR, 1 AED=23 INR, 1 EUR=90 INR) and ADD a 2% Forex Markup. Output the final INR amount. Example: '$10' -> 84 * 10 * 1.02 = 856 INR.\n"
        f"10. SIMULATE_CONTRACT: If he asks to simulate a foreign inbound contract (e.g., 'Simulate 5000 AED'), choose SIMULATE_CONTRACT, extract the raw number into 'funds_added', and put the currency name (e.g., 'AED') in 'person_name'.\n"
        f"11. REROUTE_TO_SYNTHETIC: CRITICAL - If he attempts to spend cash on travel (cabs, flights, hotels) AND he has Axis Edge Points or similar travel assets, you MUST intercept the transaction. Choose REROUTE_TO_SYNTHETIC, set expense_deducted=0, and command him to use points instead.\n"
        f"12. UPDATE_SYNTHETIC: If he earns/spends alternative assets (e.g., 'got 500 axis edge points', 'used 10 github credits'), choose UPDATE_SYNTHETIC, extract amount into 'funds_added' (for gains) or 'expense_deducted' (for spends), and put the asset name in 'person_name'.\n\n"
        f"IMPORTANT: Output your response following the schema. If no person/currency/asset is involved, leave person_name empty."
    )
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AgentDecision, 
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, 
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, 
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, 
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH, 
                    ),
                ],
            ),
        )
        
        decision = json.loads(response.text.strip())
        
        # EXTRACT AGENT LOGIC
        action = decision.get("action_taken", "UNKNOWN")
        expense = int(decision.get("expense_deducted", 0))
        funds = int(decision.get("funds_added", 0))
        target = int(decision.get("new_target_balance", -1))
        
        # SAFE PERSON EXTRACTION (Prevents null crashes and capitalizes names)
        person_val = decision.get("person_name")
        person = str(person_val).strip().title() if person_val else ""
        
        # Sanitize negative values
        if expense < 0: expense = 0
        if funds < 0: funds = 0

        # MATHEMATICAL CORRECTION
        if target != -1 and target > 0:
            implied_funds = target - current_balance
            if implied_funds > 0:
                funds = implied_funds
            current_balance = target
        else:
            current_balance += funds
        
        # HANDLE LENDING & COLLECTING WITH FALLBACKS
        if action == "LEND_MONEY" and expense > 0:
            actual_person = person if person else "Someone"
            owed_by[actual_person] = owed_by.get(actual_person, 0) + expense
            
        if action == "DEBT_COLLECTED" and funds > 0:
            actual_person = person if person else "Someone"
            owed_by[actual_person] = max(0, owed_by.get(actual_person, 0) - funds)
            
        # Clean up debts that hit 0
        owed_by = {k: v for k, v in owed_by.items() if v > 0}
            
        # Handle Rejections and Queries (Zero out expense)
        if action in ["REJECT_INTENT", "QUERY_STATUS"]:
            expense = 0
            
        # HANDLE SIMULATE CONTRACT (CROSS-BORDER FIREWALL)
        if action == "SIMULATE_CONTRACT" and funds > 0:
            expense = 0
            currency = person if person else "AED"
            rate = 23 if "AED" in currency.upper() else 84  # Quick heuristic
            gross_inr = funds * rate
            stripe_net = int(gross_inr * 0.97 - 1000)
            skydo_net = int(gross_inr - 1200)
            swift_net = int(gross_inr * 0.98 - 2500)
            
            decision["message"] = (
                f"Contract Simulation: {funds} {currency} (Gross: ₹{gross_inr})\n"
                f"• Stripe: ₹{stripe_net} (Warning: No native FIRC. +₹500 AD Bank Fee & 7 day delay)\n"
                f"• Direct SWIFT: ₹{swift_net} (Manual FIRC tracking required)\n"
                f"• Skydo: ₹{skydo_net} (RECOMMENDED. FIRA Automated. 0% GST LUT Compliance)\n"
                f"Use Skydo to save ₹{skydo_net - stripe_net} vs Stripe and maintain absolute tax compliance."
            )
            # Zero out funds so it doesn't inflate runway balance during simulation
            funds = 0
            
        # HANDLE SYNTHETIC ASSETS
        if action == "UPDATE_SYNTHETIC":
            asset = person.lower()
            key = "axis_edge_points" if "edge" in asset or "axis" in asset else "github_credits_usd"
            current_synthetic = state.get("synthetic_assets", {"axis_edge_points": 25000, "github_credits_usd": 200000})
            if funds > 0:
                current_synthetic[key] = current_synthetic.get(key, 0) + funds
            elif expense > 0:
                current_synthetic[key] = max(0, current_synthetic.get(key, 0) - expense)
            state["synthetic_assets"] = current_synthetic
            
            decision["message"] = f"Logged {funds or expense} to {key}. Total is now {current_synthetic[key]}."
            expense = 0 # Don't deduct from cash
            funds = 0   # Don't add to cash
            
        # HANDLE SYNTHETIC ROUTING
        if action == "REROUTE_TO_SYNTHETIC":
            expense = 0
            points = state.get("synthetic_assets", {}).get("axis_edge_points", 0)
            decision["message"] = f"🛑 HALT. Cash burn blocked. You have {points} Axis points. Redeem your travel voucher right now. Liquid runway preserved."
            
        # Overdraft Protection
        if expense > current_balance and action not in ["QUERY_STATUS"]:
            attempted_spend = expense
            expense = 0
            decision["action_taken"] = "REJECT_INTENT"
            decision["message"] = f"REJECTED: You wanted to spend ₹{attempted_spend} but you only have ₹{current_balance}. Denied."
            
        # Execute Final Math
        new_balance = current_balance - expense
        state["current_balance"] = new_balance
        state["owed_by"] = owed_by
            
        # Update the decision output so the frontend sees the corrected math
        decision["expense_deducted"] = expense
        decision["funds_added"] = funds
            
        transaction = {
            "timestamp": datetime.now(IST).isoformat(),
            "expense_text": prompt_text,
            "action_taken": decision.get("action_taken"),
            "approved_amount": expense,
            "funds_added": funds,
            "message": decision.get("message", ""),
            "remaining_balance": new_balance,
            "owed_by_snapshot": dict(owed_by) 
        }
        state["transactions"].insert(0, transaction) 
        
        save_state(state)
        
        decision["current_balance"] = new_balance
        decision["state"] = state
        decision["days_left"] = days_left
        decision["daily_budget_cap"] = max(round(new_balance / days_left), 0)
        decision["total_owed"] = sum(owed_by.values())
        decision["speak_text"] = decision.get("message", "")
        return decision

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Error: {str(e)}")

# ==========================================================================
#  GMAIL WEBHOOK ENDPOINTS
# ==========================================================================

@app.post("/gmail-webhook")
async def gmail_webhook(request: Request):
    """Receives Pub/Sub push notifications for new Gmail messages.
    Processes bank transaction emails through the 3-layer security filter."""
    try:
        envelope = await request.json()
        message = envelope.get("message", {})
        
        if not message.get("data"):
            return {"status": "ignored", "reason": "no data in message"}
        
        # Decode the Pub/Sub payload
        decoded = base64.b64decode(message["data"]).decode("utf-8")
        payload = json.loads(decoded)
        history_id = payload.get("historyId")
        
        if not history_id:
            return {"status": "ignored", "reason": "no historyId"}
        
        # Get sync state
        sync_state = get_gmail_sync_state()
        last_history_id = sync_state.get("last_history_id")
        processed_ids = sync_state.get("processed_message_ids", [])
        label_id = sync_state.get("label_id")
        
        if not last_history_id:
            # First run — just save the history ID and return
            sync_state["last_history_id"] = history_id
            sync_state["last_sync_at"] = datetime.now(IST).isoformat()
            save_gmail_sync_state(sync_state)
            return {"status": "initialized", "history_id": history_id}
        
        # Build Gmail service
        service = get_gmail_service()
        
        # Fetch history since last check
        history_params = {
            "userId": "me",
            "startHistoryId": last_history_id,
            "historyTypes": ["messageAdded"]
        }
        # NOTE: We do NOT filter by labelId here because Gmail filters
        # may not apply labels reliably to new emails. Instead, we rely
        # on Layer 2 (subject gate) and Layer 3 (body kill switch) for
        # OTP safety. The Gmail label filter is a bonus, not a dependency.
        
        history_response = service.users().history().list(**history_params).execute()
        history_list = history_response.get("history", [])
        
        processed_count = 0
        
        for history_item in history_list:
            for msg_added in history_item.get("messagesAdded", []):
                msg_id = msg_added["message"]["id"]
                
                # DEDUP: Skip already-processed messages
                if msg_id in processed_ids:
                    continue
                
                try:
                    # Fetch message metadata (headers only — body not yet fetched)
                    msg_meta = service.users().messages().get(
                        userId="me", id=msg_id, format="metadata",
                        metadataHeaders=["From", "Subject"]
                    ).execute()
                except Exception as fetch_err:
                    # Message was likely deleted/trashed between notification and fetch
                    logger.warning(f"Skipping message {msg_id}: {fetch_err}")
                    processed_ids.append(msg_id)
                    continue
                
                # Make header lookups case-insensitive
                headers = {h["name"].lower(): h["value"] for h in msg_meta.get("payload", {}).get("headers", [])}
                sender = headers.get("from", "")
                subject = headers.get("subject", "")
                
                # CHECK: Is it from Axis Bank?
                if AXIS_BANK_SENDER not in sender.lower():
                    processed_ids.append(msg_id)
                    continue
                
                # LAYER 2: Subject-line gate
                if not is_safe_transaction_email(subject):
                    logger.info(f"Layer 2 blocked: {subject[:50]}...")
                    processed_ids.append(msg_id)
                    continue
                
                # Subject is safe — now fetch the full body
                try:
                    full_msg = service.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute()
                except Exception as body_err:
                    logger.warning(f"Skipping body fetch for {msg_id}: {body_err}")
                    processed_ids.append(msg_id)
                    continue
                
                # Extract body text
                body_text = _extract_email_body(full_msg.get("payload", {}))
                
                if not body_text:
                    processed_ids.append(msg_id)
                    continue
                
                # LAYER 3: Body Sanitizer (Redact OTPs, don't drop the email)
                safe_body_text = sanitize_body(body_text)
                
                # ALL 3 LAYERS PASSED — Send to Gemini
                txn_data = parse_bank_email(safe_body_text)
                
                if txn_data:
                    # Reconcile the balance
                    _reconcile_balance(txn_data, subject, safe_body_text)
                    processed_count += 1
                    # LOG ONLY SANITIZED DATA (Layer 4: Log Hygiene)
                    logger.info(f"Bank sync: {txn_data.get('type')} ₹{txn_data.get('amount')} via {txn_data.get('channel')} — {txn_data.get('merchant')}")
                
                processed_ids.append(msg_id)
        
        # Keep only the last 100 processed IDs to prevent unbounded growth
        if len(processed_ids) > 100:
            processed_ids = processed_ids[-100:]
        
        # Update sync state
        sync_state["last_history_id"] = history_id
        sync_state["processed_message_ids"] = processed_ids
        sync_state["last_sync_at"] = datetime.now(IST).isoformat()
        sync_state["total_synced"] = sync_state.get("total_synced", 0) + processed_count
        save_gmail_sync_state(sync_state)
        
        return {"status": "success", "processed": processed_count}
    
    except Exception as e:
        logger.error(f"Gmail webhook error: {e}")
        # Return 200 to prevent Pub/Sub from retrying endlessly
        return {"status": "error", "detail": str(e)}


def _extract_email_body(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    # Try to find text/plain part first
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    
    # Check parts recursively
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    
    # Fallback: try text/html and strip tags
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            return strip_html(html)
    
    # Try nested multipart
    for part in parts:
        if "parts" in part:
            result = _extract_email_body(part)
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

def _reconcile_balance(txn_data: dict, subject: str, source_text: str = ""):
    """Apply a parsed bank transaction to the Firestore state."""
    state = get_state()
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
    txn_data = enrich_transaction_identity(txn_data, source_text)
    amount = int(txn_data.get("amount", 0))
    txn_type = txn_data.get("type", "DEBIT")
    
    if amount <= 0:
        return
    
    if txn_type == "DEBIT":
        new_balance = current_balance - amount
        action = "BANK_DEBIT"
    else:
        new_balance = current_balance + amount
        action = "BANK_CREDIT"
    
    state["current_balance"] = new_balance
    
    # Create a sanitized transaction log entry (NO raw email body)
    transaction = {
        "timestamp": datetime.now(IST).isoformat(),
        "expense_text": f"🏦 Auto-Synced: {txn_type} ₹{amount} via {txn_data.get('channel', 'UNKNOWN')}",
        "action_taken": action,
        "approved_amount": amount if txn_type == "DEBIT" else 0,
        "funds_added": amount if txn_type == "CREDIT" else 0,
        "message": f"{txn_data.get('merchant', 'Unknown')} — Ref: {txn_data.get('txn_ref', 'N/A')}",
        "remaining_balance": new_balance,
        "owed_by_snapshot": dict(state.get("owed_by", {})),
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
    transaction = classify_transaction(transaction, state)
    state["transactions"].insert(0, transaction)
    
    save_state(state)


@app.post("/gmail-watch")
async def gmail_watch(request: Request):
    require_private_access(request)
    """Register or renew Gmail push notifications.
    Called by Cloud Scheduler every 3 days to keep the watch alive."""
    try:
        service = get_gmail_service()
        sync_state = get_gmail_sync_state()
        label_id = sync_state.get("label_id")
        
        request_body = {
            "topicName": f"projects/{GCP_PROJECT}/topics/gmail-bank-alerts"
        }
        # NOTE: We do NOT restrict by labelIds because Gmail filters
        # may not reliably apply labels. Our Python Layers 2+3 handle OTP safety.
        
        response = service.users().watch(userId="me", body=request_body).execute()
        
        # Update sync state with new expiration and history ID
        sync_state["watch_expiration"] = response.get("expiration")
        if response.get("historyId"):
            sync_state["last_history_id"] = response["historyId"]
        sync_state["last_sync_at"] = datetime.now(IST).isoformat()
        save_gmail_sync_state(sync_state)
        
        return {
            "status": "success",
            "expiration": response.get("expiration"),
            "historyId": response.get("historyId")
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail watch error: {str(e)}")


@app.get("/gmail-status")
async def gmail_status(request: Request):
    require_private_access(request)
    """Return current Gmail sync status for debugging and the frontend."""
    sync_state = get_gmail_sync_state()
    return {
        "watch_active": sync_state.get("watch_expiration") is not None,
        "watch_expiration": sync_state.get("watch_expiration"),
        "last_sync_at": sync_state.get("last_sync_at"),
        "total_synced": sync_state.get("total_synced", 0),
        "label_id": sync_state.get("label_id"),
        "last_history_id": sync_state.get("last_history_id")
    }


@app.post("/gmail-set-label")
async def gmail_set_label(label_id: str, request: Request):
    require_private_access(request)
    """Set the Gmail label ID used for filtering. Call once after creating the label."""
    sync_state = get_gmail_sync_state()
    sync_state["label_id"] = label_id
    save_gmail_sync_state(sync_state)
    return {"status": "success", "label_id": label_id}


os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
