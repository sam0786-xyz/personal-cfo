"""Private CFO endpoints: state, intent checks, classification review, rules."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import agent, config, llm, security, state
from app.classification import (
    reclassify_similar_transactions,
    sanitize_rule,
    upsert_merchant_rule,
    validate_category,
)

logger = logging.getLogger("personal-cfo")
router = APIRouter()


class ExpenseRequest(BaseModel):
    expense_text: str | None = None
    text: str | None = None
    input: str | None = None
    query: str | None = None

    def prompt_text(self):
        return (self.expense_text or self.text or self.input or self.query or "").strip()


class ClassificationCorrection(BaseModel):
    category: str
    subcategory: str = ""
    always_apply: bool = True


class MerchantRuleRequest(BaseModel):
    pattern: str
    category: str
    subcategory: str = ""
    match_type: str = "payee"


class BillingAlert(BaseModel):
    daily_burn_usd: float


@router.get("/cfo-state")
async def cfo_state(request: Request):
    security.require_private_access(request)
    current = state.load_state()
    return {
        **current,
        "days_left": config.days_until_target(),
        "target_date": config.target_date_iso(),
    }


@router.post("/cfo-check")
async def cfo_check(expense_request: ExpenseRequest, request: Request):
    security.require_private_access(request)
    prompt_text = expense_request.prompt_text()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Send JSON with expense_text, text, input, or query.")
    if llm.get_client() is None:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured.")

    days_left = config.days_until_target()
    # Read once to build the prompt; the mutation below re-reads inside a
    # Firestore transaction, so a bank sync landing mid-call cannot be lost.
    prompt_state = state.load_state()
    system_instruction = agent.build_agent_prompt(prompt_state, days_left)

    try:
        decision = llm.run_agent_decision(prompt_text, system_instruction)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Error: {str(e)}")

    try:
        final_state, response = state.mutate_state(
            lambda s: agent.apply_agent_decision(s, decision, prompt_text, days_left)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent Error: {str(e)}")

    response["state"] = final_state
    return response


@router.post("/cfo-reset")
async def cfo_reset(request: Request):
    security.require_private_access(request)

    def reset(s: dict):
        s["current_balance"] = config.DEFAULT_BALANCE
        s["transactions"] = []
        s["owed_by"] = {}

    new_state, _ = state.mutate_state(reset)
    return {
        "status": "success",
        "message": f"Runway reset to ₹{config.DEFAULT_BALANCE:,}.",
        "state": new_state,
    }


@router.get("/transactions/review")
async def transactions_review(request: Request):
    security.require_private_access(request)
    current = state.load_state()
    transactions = [
        tx for tx in current.get("transactions", [])
        if tx.get("needs_review") and tx.get("action_taken") == "BANK_DEBIT"
    ]
    return {"transactions": transactions}


@router.post("/transactions/{transaction_id}/classify")
async def classify_existing_transaction(transaction_id: str, correction: ClassificationCorrection, request: Request):
    security.require_private_access(request)
    category = validate_category(correction.category)

    def correct(s: dict):
        target = None
        for tx in s.get("transactions", []):
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
            pattern = (
                target.get("upi_vpa")
                or target.get("payee_normalized")
                or target.get("merchant_normalized")
                or target.get("merchant")
            )
            rule = sanitize_rule({
                "pattern": pattern,
                "match_type": "upi_vpa" if target.get("upi_vpa") else "payee",
                "category": category,
                "subcategory": correction.subcategory,
                "source": "learned",
                "confidence": 0.98,
            })
            if rule:
                upsert_merchant_rule(s, rule)
                reclassified = reclassify_similar_transactions(s, rule, skip_id=transaction_id)
        return {"transaction": target, "merchant_rule": rule, "reclassified": reclassified}

    _, result = state.mutate_state(correct)
    return {"status": "success", **result}


@router.get("/merchant-rules")
async def merchant_rules(request: Request):
    security.require_private_access(request)
    current = state.load_state()
    return {"merchant_rules": current.get("merchant_rules", [])}


@router.post("/merchant-rules")
async def create_merchant_rule(rule_request: MerchantRuleRequest, request: Request):
    security.require_private_access(request)
    category = validate_category(rule_request.category)
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

    def apply_rule(s: dict):
        upsert_merchant_rule(s, rule)
        return reclassify_similar_transactions(s, rule)

    _, reclassified = state.mutate_state(apply_rule)
    return {"status": "success", "merchant_rule": rule, "reclassified": reclassified}


@router.post("/gcp-billing-webhook")
async def gcp_billing_webhook(alert: BillingAlert, request: Request):
    security.require_private_access(request)

    def update_burn(s: dict):
        billing = s.get("gcp_billing", {})
        billing["vertex_daily_burn_usd"] = alert.daily_burn_usd
        s["gcp_billing"] = billing

    state.mutate_state(update_burn)
    return {"status": "success", "new_burn": alert.daily_burn_usd}
