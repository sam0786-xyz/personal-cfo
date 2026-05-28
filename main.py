from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv
from datetime import datetime
import json
import os
from google.cloud import firestore

load_dotenv()

app = FastAPI(title="Action Button CFO Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

db = firestore.Client()
doc_ref = db.collection("cfo_state").document("wallet")
DEFAULT_BALANCE = 5000

def get_state():
    doc = doc_ref.get()
    if doc.exists:
        state = doc.to_dict()
        # Initialize owed_by ledger if it doesn't exist from older versions
        if "owed_by" not in state:
            state["owed_by"] = {}
        return state
    return {"current_balance": 5000, "transactions": [], "owed_by": {}} 

def save_state(state):
    doc_ref.set(state)

class ExpenseRequest(BaseModel):
    expense_text: str

# Define the exact JSON schema the Agent MUST return
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

@app.post("/cfo-reset")
async def cfo_reset():
    state = {"current_balance": DEFAULT_BALANCE, "transactions": [], "owed_by": {}}
    save_state(state)
    return {"status": "success", "message": "Runway reset to ₹5,000.", "state": state}

@app.post("/cfo-check")
async def cfo_check(request: ExpenseRequest):
    state = get_state()
    current_balance = state.get("current_balance", DEFAULT_BALANCE)
    owed_by = state.get("owed_by", {})
    
    target_date = datetime(2026, 6, 22)
    today = datetime.now()
    days_left = max((target_date - today).days, 1)
    
    # Format current debts so the agent can read them
    debt_string = ", ".join([f"{name} owes ₹{amt}" for name, amt in owed_by.items() if amt > 0])
    if not debt_string: debt_string = "Nobody owes him money."
    
    # THE UPGRADED STATE-MACHINE PROMPT
    system_instruction = (
        f"You are a lightning-fast autonomous financial agent for a 22-year-old AI engineer.\n"
        f"CURRENT STATE:\n- Net worth until June 22nd ({days_left} days left): ₹{current_balance}\n"
        f"- Debts (Money owed to him): {debt_string}\n\n"
        f"YOUR CAPABILITIES:\n"
        f"1. SET_EXACT_BALANCE: If he says his money 'became' or 'is now' a specific amount, set new_target_balance.\n"
        f"2. ADD_FUNDS: If he receives extra money (e.g., stipends).\n"
        f"3. RETROACTIVE_DEDUCTION: If he ALREADY spent money. You MUST deduct it, but roast him.\n"
        f"4. REJECT_INTENT: If he ASKS to spend on something stupid. Reject it.\n"
        f"5. APPROVE_INTENT: If he ASKS to spend on something necessary.\n"
        f"6. LEND_MONEY: If he gives money to someone, deduct it and set person_name.\n"
        f"7. DEBT_COLLECTED: If someone pays him back, add funds and set person_name to reduce their debt.\n"
        f"8. QUERY_STATUS: If he asks 'Who owes me money?' or 'What is my balance?', answer him based on the CURRENT STATE above. Do not deduct anything.\n\n"
        f"IMPORTANT: Output your response following the schema. If an integer is not needed, output 0. If new_target_balance is not used, output -1. If no person is involved, leave person_name empty."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=request.expense_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AgentDecision, # Native Tooling for massive speed increase
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
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
        person = decision.get("person_name", "").strip()
        
        if expense < 0: expense = 0
        if funds < 0: funds = 0

        # 1. Handle Target Balances (Overrides current balance)
        if target != -1:
            current_balance = target
            
        # 2. Handle standard income
        current_balance += funds
        
        # 3. Handle Lending & Collecting
        if action == "LEND_MONEY" and person and expense > 0:
            owed_by[person] = owed_by.get(person, 0) + expense
            
        if action == "DEBT_COLLECTED" and person and funds > 0:
            owed_by[person] = max(0, owed_by.get(person, 0) - funds)
            
        # 4. Handle Rejections and Queries (Zero out expense)
        if action in ["REJECT_INTENT", "QUERY_STATUS"]:
            expense = 0
            
        # 5. Overdraft Protection
        if expense > current_balance and action not in ["QUERY_STATUS"]:
            attempted_spend = expense
            expense = 0
            decision["action_taken"] = "REJECT_INTENT"
            decision["message"] = f"REJECTED: You wanted to spend ₹{attempted_spend} but you only have ₹{current_balance}. Denied."
            
        # Execute Final Math
        new_balance = current_balance - expense
        state["current_balance"] = new_balance
        state["owed_by"] = owed_by
            
        transaction = {
            "timestamp": datetime.now().isoformat(),
            "expense_text": request.expense_text,
            "action_taken": decision.get("action_taken"),
            "approved_amount": expense,
            "message": decision.get("message", ""),
            "remaining_balance": new_balance,
            "owed_by_snapshot": dict(owed_by) # Save history of who owed what at this moment
        }
        state["transactions"].insert(0, transaction) 
        
        save_state(state)
        
        decision["current_balance"] = new_balance
        return decision

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Error: {str(e)}")

os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")