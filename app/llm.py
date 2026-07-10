"""Gemini client and structured-output calls.

The client is created lazily so the app (and the test suite) can import
without credentials. All calls use response schemas — the model never
returns free-form text into the pipeline.
"""

import json
import logging

from google import genai
from google.genai import types
from pydantic import BaseModel

from app import config

logger = logging.getLogger("personal-cfo")

_client = None
_client_initialized = False


def get_client():
    global _client, _client_initialized
    if not _client_initialized:
        _client = genai.Client(api_key=config.GEMINI_API_KEY) if config.GEMINI_API_KEY else None
        _client_initialized = True
    return _client


SAFETY_SETTINGS = [
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
]


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


class TransactionClassification(BaseModel):
    category: str
    subcategory: str = ""
    confidence: float
    reason: str


class AgentDecision(BaseModel):
    message: str
    action_taken: str
    expense_deducted: int
    funds_added: int
    new_target_balance: int
    person_name: str


BANK_PARSER_PROMPT = (
    "You are a bank transaction data extractor. Extract EXACTLY the following from this bank email:\n"
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


def parse_bank_email(body_text: str) -> dict | None:
    """Extract structured transaction data from a (pre-sanitized) bank email."""
    client = get_client()
    if client is None:
        logger.error("Gemini client not configured.")
        return None
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=body_text,
            config=types.GenerateContentConfig(
                system_instruction=BANK_PARSER_PROMPT,
                response_mime_type="application/json",
                response_schema=BankTransaction,
                safety_settings=SAFETY_SETTINGS,
            ),
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logger.error(f"Gemini bank email parsing error: {e}")
        return None


def classify_structured_transaction(sanitized_payload: dict, categories: set[str]) -> dict | None:
    """LLM fallback classifier. Receives only structured fields — never raw
    email text — and returns None below the confidence threshold so the
    transaction lands in the human review queue instead."""
    client = get_client()
    if client is None:
        return None
    system_prompt = (
        "Classify this Indian payment transaction. Use only the provided structured fields. "
        "Do not infer from location, calendar, screenshots, or any unavailable context. "
        f"Category must be one of: {', '.join(sorted(categories))}. "
        "For generic person-to-person UPI or ambiguous QR payments, choose Unknown or Transfer; never force Food/Shopping. "
        "If a known brand has multiple meanings, such as Uber rides vs Uber Eats, use the food/ride words in the structured fields. "
        "Set confidence below 0.78 when the fields are not enough; those will go to user review."
    )
    try:
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=json.dumps(sanitized_payload),
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=TransactionClassification,
            ),
        )
        return json.loads(response.text.strip())
    except Exception as e:
        logger.warning(f"LLM classification skipped: {e}")
        return None


def run_agent_decision(prompt_text: str, system_instruction: str) -> dict:
    """Run the CFO agent prompt and return the structured decision."""
    client = get_client()
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt_text,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=AgentDecision,
            safety_settings=SAFETY_SETTINGS,
        ),
    )
    return json.loads(response.text.strip())
