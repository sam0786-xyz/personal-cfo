from fastapi import FastAPI, HTTPException
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
from google.cloud import firestore

load_dotenv()

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
DEFAULT_BALANCE = 5000
TARGET_DATE = datetime(2026, 6, 22, tzinfo=IST)

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

os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
