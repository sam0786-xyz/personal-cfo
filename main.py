from fastapi import FastAPI, HTTPException, Request
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
        "- txn_ref: The transaction reference number if present, otherwise empty string\n\n"
        "Output ONLY the structured JSON. Do NOT hallucinate amounts. Extract the EXACT numbers from the email."
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

def normalize_state(state):
    state = state or {}
    current_balance = int(state.get("current_balance", DEFAULT_BALANCE))
    transactions = state.get("transactions") or []
    owed_by = state.get("owed_by") or {}
    owed_by = {
        str(name).strip().title(): int(amount)
        for name, amount in owed_by.items()
        if str(name).strip() and int(amount) > 0
    }
    return {
        "current_balance": current_balance,
        "transactions": transactions,
        "owed_by": owed_by,
        "updated_at": state.get("updated_at"),
    }

def get_state():
    doc = doc_ref.get()
    if doc.exists:
        return normalize_state(doc.to_dict())
    return normalize_state({"current_balance": DEFAULT_BALANCE, "transactions": [], "owed_by": {}})

def save_state(state):
    state["updated_at"] = datetime.now(IST).isoformat()
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

@app.get("/cfo-state")
async def cfo_state():
    return get_state()

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "personal-cfo"}

@app.post("/cfo-reset")
async def cfo_reset():
    state = {"current_balance": DEFAULT_BALANCE, "transactions": [], "owed_by": {}}
    save_state(state)
    return {"status": "success", "message": "Runway reset to ₹5,000.", "state": state}

@app.post("/cfo-check")
async def cfo_check(request: ExpenseRequest):
    prompt_text = request.prompt_text()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Send JSON with expense_text, text, input, or query.")
    if client is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    state = get_state()
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
    owed_by = state.get("owed_by", {})
    
    # Clean up any zero-balance debts from previous runs
    owed_by = {k: v for k, v in owed_by.items() if v > 0}
    
    days_left = days_until_target()
    
    debt_string = ", ".join([f"{name} owes ₹{amt}" for name, amt in owed_by.items()])
    if not debt_string: debt_string = "Nobody owes him money."
    
    # THE BULLETPROOF AGENT PROMPT
    system_instruction = (
        f"You are a lightning-fast autonomous financial agent for a 22-year-old AI engineer.\n"
        f"CURRENT STATE:\n- Net worth until June 22nd ({days_left} days left): ₹{current_balance}\n"
        f"- Debts (Money owed to him): {debt_string}\n\n"
        f"CRITICAL RULE: DO NOT DO ANY MATH. DO NOT ADD OR SUBTRACT. Only extract the EXACT raw numbers stated by the user.\n"
        f"YOUR CAPABILITIES:\n"
        f"1. ADD_FUNDS: If he explicitly receives extra money, extract the exact amount into 'funds_added'.\n"
        f"2. SET_EXACT_BALANCE: ONLY if he explicitly states his total balance 'became' or 'is now' a specific amount, put that amount in 'new_target_balance'.\n"
        f"3. RETROACTIVE_DEDUCTION: If he ALREADY spent money, extract the amount into 'expense_deducted' and roast him.\n"
        f"4. REJECT_INTENT: If he ASKS to spend on something stupid, set expense_deducted to 0 and reject it.\n"
        f"5. APPROVE_INTENT: If he ASKS to spend on goods/services for himself, extract amount into 'expense_deducted'.\n"
        f"6. LEND_MONEY: CRITICAL - If the transaction involves giving/lending money TO A SPECIFIC PERSON (e.g., 'Faizan', 'my brother'), you MUST choose LEND_MONEY, not Approve Intent. Extract amount into 'expense_deducted' and their name into 'person_name'.\n"
        f"7. DEBT_COLLECTED: CRITICAL - If a person returns/pays back money to him, choose DEBT_COLLECTED. Extract amount into 'funds_added' and their name into 'person_name'.\n"
        f"8. QUERY_STATUS: If he asks for his balance or debts, output 0 for all amounts.\n\n"
        f"IMPORTANT: Output your response following the schema. If no person is involved, leave person_name empty."
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
                
                # Fetch message metadata (headers only — body not yet fetched)
                msg_meta = service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "Subject"]
                ).execute()
                
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
                full_msg = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()
                
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
                    _reconcile_balance(txn_data, subject)
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


def _reconcile_balance(txn_data: dict, subject: str):
    """Apply a parsed bank transaction to the Firestore state."""
    state = get_state()
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
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
        "channel": txn_data.get("channel", "UNKNOWN")
    }
    state["transactions"].insert(0, transaction)
    
    save_state(state)


@app.post("/gmail-watch")
async def gmail_watch():
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
async def gmail_status():
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
async def gmail_set_label(label_id: str):
    """Set the Gmail label ID used for filtering. Call once after creating the label."""
    sync_state = get_gmail_sync_state()
    sync_state["label_id"] = label_id
    save_gmail_sync_state(sync_state)
    return {"status": "success", "label_id": label_id}


os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
