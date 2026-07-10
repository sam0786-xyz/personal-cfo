"""Tests for Gmail payload body extraction and transaction enrichment."""

import base64

from app.gmail_pipeline import (
    apply_bank_transaction,
    enrich_transaction_identity,
    extract_email_body,
    strip_html,
)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


class TestExtractEmailBody:
    def test_top_level_plain_text(self):
        payload = {"mimeType": "text/plain", "body": {"data": b64("INR 340 debited")}}
        assert extract_email_body(payload) == "INR 340 debited"

    def test_plain_text_part_preferred_over_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": b64("<b>html version</b>")}},
                {"mimeType": "text/plain", "body": {"data": b64("plain version")}},
            ],
        }
        assert extract_email_body(payload) == "plain version"

    def test_html_fallback_strips_tags(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/html", "body": {"data": b64("<p>INR <b>340</b> debited</p>")}},
            ],
        }
        assert extract_email_body(payload) == "INR 340 debited"

    def test_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": b64("nested body")}},
                    ],
                },
            ],
        }
        assert extract_email_body(payload) == "nested body"

    def test_empty_payload(self):
        assert extract_email_body({}) == ""

    def test_strip_html(self):
        assert strip_html("<div>Hello <span>world</span></div>") == "Hello world"


class TestEnrichTransactionIdentity:
    def test_extracts_vpa_from_source_text(self):
        txn = {"amount": 340, "type": "DEBIT", "merchant": "CHAI POINT", "channel": "UPI", "txn_ref": "r1"}
        enriched = enrich_transaction_identity(txn, "paid to chaipoint@okaxis via UPI")
        assert enriched["upi_vpa"] == "chaipoint@okaxis"
        assert enriched["upi_handle"] == "@okaxis"
        assert enriched["direction"] == "OUTBOUND"

    def test_credit_direction(self):
        txn = {"amount": 500, "type": "CREDIT", "merchant": "ACME", "channel": "NEFT", "txn_ref": ""}
        assert enrich_transaction_identity(txn)["direction"] == "INBOUND"

    def test_missing_merchant_falls_back(self):
        txn = {"amount": 100, "type": "DEBIT", "merchant": "", "channel": "UPI", "txn_ref": ""}
        assert enrich_transaction_identity(txn, "sent to ramesh@ybl")["merchant"] == "ramesh@ybl"


class TestApplyBankTransaction:
    def make_state(self, balance=5000):
        return {
            "current_balance": balance,
            "transactions": [],
            "owed_by": {},
            "merchant_rules": [],
        }

    def test_debit_reduces_balance(self):
        state = self.make_state(5000)
        txn = enrich_transaction_identity(
            {"amount": 340, "type": "DEBIT", "merchant": "SWIGGY", "channel": "UPI", "txn_ref": "ref1"}
        )
        created = apply_bank_transaction(state, txn)
        assert state["current_balance"] == 4660
        assert created["action_taken"] == "BANK_DEBIT"
        assert created["category"] == "Food"  # classified inline via keyword rule
        assert created["source"] == "GMAIL_SYNC"

    def test_credit_increases_balance(self):
        state = self.make_state(5000)
        txn = enrich_transaction_identity(
            {"amount": 1000, "type": "CREDIT", "merchant": "ACME LLP", "channel": "NEFT", "txn_ref": "ref2"}
        )
        created = apply_bank_transaction(state, txn)
        assert state["current_balance"] == 6000
        assert created["action_taken"] == "BANK_CREDIT"
        assert created["category"] == "Income"

    def test_invalid_amount_is_ignored(self):
        state = self.make_state(5000)
        txn = enrich_transaction_identity(
            {"amount": 0, "type": "DEBIT", "merchant": "X", "channel": "UPI", "txn_ref": ""}
        )
        assert apply_bank_transaction(state, txn) is None
        assert state["current_balance"] == 5000
        assert state["transactions"] == []

    def test_never_stores_raw_email_body(self):
        raw_body = "Dear customer, INR 340 debited ... long raw email body ... regards, bank"
        state = self.make_state(5000)
        txn = enrich_transaction_identity(
            {"amount": 340, "type": "DEBIT", "merchant": "SWIGGY", "channel": "UPI", "txn_ref": "ref1"},
            raw_body,
        )
        created = apply_bank_transaction(state, txn)
        serialized = str(created) + str(state)
        assert "Dear customer" not in serialized
        assert "regards, bank" not in serialized
