"""The CFO agent: prompt construction and deterministic decision application.

The LLM only extracts intent and raw numbers; every rupee of arithmetic,
overdraft protection, debt bookkeeping, and asset routing happens here in
plain Python so it can be unit-tested and never hallucinated.
"""

from datetime import datetime

from app import config
from app.config import IST


def build_agent_prompt(state: dict, days_left: int) -> str:
    current_balance = state.get("current_balance", config.DEFAULT_BALANCE)
    owed_by = state.get("owed_by", {})

    debt_string = ", ".join([f"{name} owes ₹{amt}" for name, amt in owed_by.items()])
    if not debt_string:
        debt_string = "Nobody owes him money."

    synthetic_str = ", ".join([f"{k}: {v}" for k, v in state.get("synthetic_assets", {}).items()])
    if not synthetic_str:
        synthetic_str = "None"

    return (
        f"You are a lightning-fast autonomous financial agent for {config.CFO_PERSONA}.\n"
        f"CURRENT STATE:\n- Net worth for the next {days_left} days: ₹{current_balance}\n"
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


def apply_agent_decision(state: dict, decision: dict, prompt_text: str, days_left: int) -> dict:
    """Apply the LLM's structured decision to the wallet state.

    Mutates ``state`` in place and returns the response payload for the
    client. Pure with respect to I/O — no network, no database.
    """
    current_balance = state.get("current_balance", config.DEFAULT_BALANCE)
    owed_by = {k: v for k, v in state.get("owed_by", {}).items() if v > 0}

    action = decision.get("action_taken", "UNKNOWN")
    expense = int(decision.get("expense_deducted", 0) or 0)
    funds = int(decision.get("funds_added", 0) or 0)
    target = int(decision.get("new_target_balance", -1) or -1)

    person_val = decision.get("person_name")
    person = str(person_val).strip().title() if person_val else ""

    expense = max(expense, 0)
    funds = max(funds, 0)

    # The model extracts raw numbers; the math happens here.
    if target != -1 and target > 0:
        implied_funds = target - current_balance
        if implied_funds > 0:
            funds = implied_funds
        current_balance = target
    else:
        current_balance += funds

    if action == "LEND_MONEY" and expense > 0:
        actual_person = person if person else "Someone"
        owed_by[actual_person] = owed_by.get(actual_person, 0) + expense

    if action == "DEBT_COLLECTED" and funds > 0:
        actual_person = person if person else "Someone"
        owed_by[actual_person] = max(0, owed_by.get(actual_person, 0) - funds)

    owed_by = {k: v for k, v in owed_by.items() if v > 0}

    if action in ["REJECT_INTENT", "QUERY_STATUS"]:
        expense = 0

    if action == "SIMULATE_CONTRACT" and funds > 0:
        expense = 0
        currency = (person if person else "USD").upper()
        rate = config.FOREX_RATES_INR.get(currency[:3], config.USD_INR)
        gross_inr = int(funds * rate)
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
        # A simulation must never inflate the real runway balance.
        current_balance -= funds
        funds = 0

    if action == "UPDATE_SYNTHETIC":
        asset = person.lower()
        key = "axis_edge_points" if "edge" in asset or "axis" in asset else "github_credits_usd"
        current_synthetic = state.get("synthetic_assets", {})
        if funds > 0:
            current_synthetic[key] = current_synthetic.get(key, 0) + funds
        elif expense > 0:
            current_synthetic[key] = max(0, current_synthetic.get(key, 0) - expense)
        state["synthetic_assets"] = current_synthetic

        decision["message"] = f"Logged {funds or expense} to {key}. Total is now {current_synthetic.get(key, 0)}."
        # Synthetic assets never touch the cash balance.
        current_balance -= funds
        expense = 0
        funds = 0

    if action == "REROUTE_TO_SYNTHETIC":
        expense = 0
        points = state.get("synthetic_assets", {}).get("axis_edge_points", 0)
        decision["message"] = (
            f"🛑 HALT. Cash burn blocked. You have {points} Axis points. "
            f"Redeem your travel voucher right now. Liquid runway preserved."
        )

    if expense > current_balance and action != "QUERY_STATUS":
        attempted_spend = expense
        expense = 0
        decision["action_taken"] = "REJECT_INTENT"
        decision["message"] = (
            f"REJECTED: You wanted to spend ₹{attempted_spend} but you only have ₹{current_balance}. Denied."
        )

    new_balance = current_balance - expense
    state["current_balance"] = new_balance
    state["owed_by"] = owed_by

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
        "owed_by_snapshot": dict(owed_by),
    }
    state["transactions"].insert(0, transaction)

    decision["current_balance"] = new_balance
    decision["days_left"] = days_left
    decision["daily_budget_cap"] = max(round(new_balance / days_left), 0)
    decision["total_owed"] = sum(owed_by.values())
    decision["speak_text"] = decision.get("message", "")
    return decision
