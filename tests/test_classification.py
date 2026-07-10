"""Tests for the classification cascade and rule learning."""

import pytest
from fastapi import HTTPException

from app.classification import (
    apply_keyword_rule,
    apply_learned_rule,
    apply_recurring_pattern,
    classify_transaction,
    reclassify_similar_transactions,
    sanitize_rule,
    upsert_merchant_rule,
    validate_category,
)
from app.normalization import extract_upi_vpa, normalize_payee, upi_handle_from_vpa


def bank_debit(merchant, amount=100, **overrides):
    tx = {
        "action_taken": "BANK_DEBIT",
        "direction": "OUTBOUND",
        "approved_amount": amount,
        "merchant": merchant,
        "payee_raw": merchant,
        "channel": "UPI",
    }
    tx.update(overrides)
    return tx


class TestNormalization:
    def test_normalize_payee_strips_bank_boilerplate(self):
        assert normalize_payee("UPI payment to SWIGGY ref 12345") == "swiggy 12345"

    def test_normalize_payee_handles_empty(self):
        assert normalize_payee("") == ""
        assert normalize_payee(None) == ""

    def test_extract_upi_vpa(self):
        assert extract_upi_vpa("paid to vendor@oksbi via UPI") == "vendor@oksbi"
        assert extract_upi_vpa("no vpa here", "chai.wala-01@paytm") == "chai.wala-01@paytm"
        assert extract_upi_vpa("nothing") == ""

    def test_upi_handle_from_vpa(self):
        assert upi_handle_from_vpa("vendor@oksbi") == "@oksbi"
        assert upi_handle_from_vpa("") == ""


class TestKeywordRules:
    def test_known_food_merchant(self):
        result = apply_keyword_rule(bank_debit("SWIGGY BANGALORE"))
        assert result["category"] == "Food"
        assert result["classification_source"] == "system"

    def test_uber_alone_is_transport(self):
        result = apply_keyword_rule(bank_debit("UBER INDIA SYSTEMS"))
        assert result["category"] == "Transport"

    def test_ambiguous_uber_eats_is_skipped(self):
        # "uber" + "eats" conflict → keyword stage abstains so a later stage
        # (or the user) decides instead of guessing wrong.
        assert apply_keyword_rule(bank_debit("UBER EATS ORDER")) is None

    def test_unknown_merchant_returns_none(self):
        assert apply_keyword_rule(bank_debit("RAMESH KUMAR")) is None


class TestLearnedRules:
    def test_learned_rule_matches_vpa(self):
        rule = sanitize_rule({"pattern": "chaiwala@oksbi", "category": "Food"})
        tx = bank_debit("Paid via UPI", upi_vpa="chaiwala@oksbi")
        tx["payee_normalized"] = "chaiwala@oksbi"
        result = apply_learned_rule(tx, [rule])
        assert result["category"] == "Food"
        assert result["needs_review"] is False

    def test_no_match_returns_none(self):
        rule = sanitize_rule({"pattern": "chaiwala@oksbi", "category": "Food"})
        tx = bank_debit("other merchant", payee_normalized="other merchant", merchant_normalized="other merchant")
        assert apply_learned_rule(tx, [rule]) is None

    def test_upsert_deduplicates_by_pattern(self):
        state = {"merchant_rules": []}
        rule = sanitize_rule({"pattern": "chaiwala@oksbi", "category": "Food"})
        upsert_merchant_rule(state, dict(rule))
        updated = sanitize_rule({"pattern": "chaiwala@oksbi", "category": "Transport"})
        upsert_merchant_rule(state, updated)
        assert len(state["merchant_rules"]) == 1
        assert state["merchant_rules"][0]["category"] == "Transport"

    def test_reclassify_similar_transactions(self):
        rule = sanitize_rule({"pattern": "chaiwala@oksbi", "category": "Food"})
        pending = bank_debit("chai", upi_vpa="chaiwala@oksbi", needs_review=True, id="tx1")
        settled = bank_debit("chai", upi_vpa="chaiwala@oksbi", needs_review=False, id="tx2", category="Transport")
        state = {"transactions": [pending, settled]}
        count = reclassify_similar_transactions(state, rule)
        assert count == 1
        assert pending["category"] == "Food"
        assert pending["needs_review"] is False
        assert settled["category"] == "Transport"  # already-settled rows untouched


class TestRecurringPattern:
    def test_two_prior_matches_classify(self):
        history = [
            bank_debit("GYM FIT", amount=999, id=f"h{i}", merchant_normalized="gym fit", category="Healthcare")
            for i in range(2)
        ]
        tx = bank_debit("GYM FIT", amount=999, id="new", merchant_normalized="gym fit")
        result = apply_recurring_pattern(tx, history)
        assert result["category"] == "Healthcare"
        assert result["classification_source"] == "recurring"

    def test_single_prior_match_is_not_enough(self):
        history = [bank_debit("GYM FIT", amount=999, id="h0", merchant_normalized="gym fit", category="Healthcare")]
        tx = bank_debit("GYM FIT", amount=999, id="new", merchant_normalized="gym fit")
        assert apply_recurring_pattern(tx, history) is None

    def test_amount_tolerance(self):
        history = [
            bank_debit("GYM FIT", amount=1000, id=f"h{i}", merchant_normalized="gym fit", category="Healthcare")
            for i in range(2)
        ]
        close = bank_debit("GYM FIT", amount=1020, id="new", merchant_normalized="gym fit")
        far = bank_debit("GYM FIT", amount=1500, id="new2", merchant_normalized="gym fit")
        assert apply_recurring_pattern(close, history) is not None
        assert apply_recurring_pattern(far, history) is None


class TestCascade:
    def test_credit_is_income(self):
        tx = {"action_taken": "BANK_CREDIT", "direction": "INBOUND", "funds_added": 500, "merchant": "ACME LLP"}
        result = classify_transaction(tx, {"merchant_rules": [], "transactions": []})
        assert result["category"] == "Income"
        assert result["needs_review"] is False

    def test_refund_credit_is_refund(self):
        tx = {"action_taken": "BANK_CREDIT", "direction": "INBOUND", "funds_added": 250, "merchant": "AMAZON REFUND"}
        result = classify_transaction(tx, {"merchant_rules": [], "transactions": []})
        assert result["category"] == "Refund"

    def test_learned_rule_beats_keyword_rule(self):
        # "cafe" would keyword-match Food, but the user taught us otherwise.
        rule = sanitize_rule({"pattern": "internet cafe hub", "category": "Business/Tools"})
        tx = bank_debit("Internet Cafe Hub", merchant_normalized="internet cafe hub", payee_normalized="internet cafe hub")
        result = classify_transaction(tx, {"merchant_rules": [rule], "transactions": []})
        assert result["category"] == "Business/Tools"
        assert result["classification_source"] == "learned"

    def test_unresolvable_transaction_goes_to_review(self):
        # No rules, no history, no LLM configured → human review queue.
        tx = bank_debit("RAMESH KUMAR", upi_vpa="ramesh.k@ybl")
        result = classify_transaction(tx, {"merchant_rules": [], "transactions": []})
        assert result["category"] == "Unknown"
        assert result["needs_review"] is True
        assert result["classification_source"] == "review"


class TestValidateCategory:
    def test_accepts_known_category(self):
        assert validate_category("Food") == "Food"
        assert validate_category("  Travel ") == "Travel"

    def test_rejects_unknown_category(self):
        with pytest.raises(HTTPException) as exc:
            validate_category("Gambling")
        assert exc.value.status_code == 400
