"""Adversarial tests for the OTP firewall and Pub/Sub push verification."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import config, security
from app.security import is_safe_transaction_email, sanitize_body, verify_pubsub_push


class TestSubjectGate:
    """Layer 2: the subject gate must default-deny and hard-block credentials."""

    @pytest.mark.parametrize("subject", [
        "Transaction alert: INR 340.00 debited from A/c no XX1234",
        "Rs 500 credited to your account via UPI",
        "NEFT transfer successful",
        "Your EMI payment was processed",
        "Refund of INR 250 processed",
    ])
    def test_allows_transaction_subjects(self, subject):
        assert is_safe_transaction_email(subject) is True

    @pytest.mark.parametrize("subject", [
        "Your OTP for net banking is inside",
        "One Time Password for your request",
        "Reset your password",
        "Verification code for login",
        "Your debit card PIN has been generated",
        "2FA security code",
    ])
    def test_blocks_credential_subjects(self, subject):
        assert is_safe_transaction_email(subject) is False

    def test_block_keyword_overrides_transaction_keyword(self):
        # An attacker-crafted (or bank-crafted) subject mixing both must lose.
        assert is_safe_transaction_email("OTP for your UPI payment of INR 500") is False
        assert is_safe_transaction_email("Use this One Time Password to confirm the debited amount") is False

    def test_unknown_subjects_are_denied_by_default(self):
        assert is_safe_transaction_email("Monthly statement is ready") is False
        assert is_safe_transaction_email("Welcome to Axis Bank!") is False
        assert is_safe_transaction_email("") is False
        assert is_safe_transaction_email(None) is False


class TestBodySanitizer:
    """Layer 3: OTP-shaped content must be redacted before the LLM sees it."""

    @pytest.mark.parametrize("body", [
        "Your OTP is 482913. Do not share it.",
        "OTP: 4829 for txn at Amazon",
        "Use PIN 553311 to authorise",
        "password 998877",
        "code-123456",
    ])
    def test_redacts_credentials(self, body):
        sanitized = sanitize_body(body)
        for digits in ["482913", "4829", "553311", "998877", "123456"]:
            assert digits not in sanitized
        assert "[BLOCKED]" in sanitized

    def test_redacts_standalone_six_digit_numbers(self):
        assert "774411" not in sanitize_body("Confirm with 774411 to proceed")

    def test_preserves_transaction_amounts(self):
        body = "INR 340.00 debited from A/c XX1234 at SWIGGY. Ref 99887766554. Never share your OTP with anyone."
        sanitized = sanitize_body(body)
        assert "340.00" in sanitized
        assert "SWIGGY" in sanitized
        # Indian bank amounts carry comma separators, so they survive the 6-digit rule.
        assert "1,23,456" in sanitize_body("Rs 1,23,456.00 credited via NEFT")

    def test_footer_warning_does_not_kill_the_email(self):
        body = "Rs 500 debited via UPI at ZOMATO. Never share your OTP 123456 with anyone."
        sanitized = sanitize_body(body)
        assert "ZOMATO" in sanitized
        assert "123456" not in sanitized

    def test_handles_empty_body(self):
        assert sanitize_body("") == ""
        assert sanitize_body(None) == ""


class TestPubSubVerification:
    def _request(self, headers=None):
        return SimpleNamespace(headers=headers or {})

    def test_skips_when_audience_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", None)
        verify_pubsub_push(self._request())  # must not raise

    def test_rejects_missing_token(self, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
        with pytest.raises(HTTPException) as exc:
            verify_pubsub_push(self._request())
        assert exc.value.status_code == 401

    def test_rejects_invalid_token(self, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
        monkeypatch.setattr(
            security.id_token, "verify_oauth2_token",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("bad token")),
        )
        with pytest.raises(HTTPException) as exc:
            verify_pubsub_push(self._request({"authorization": "Bearer forged"}))
        assert exc.value.status_code == 401

    def test_rejects_unexpected_service_account(self, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
        monkeypatch.setattr(config, "PUBSUB_SERVICE_ACCOUNT", "push@my-project.iam.gserviceaccount.com")
        monkeypatch.setattr(
            security.id_token, "verify_oauth2_token",
            lambda *a, **k: {"email": "attacker@evil.iam.gserviceaccount.com", "email_verified": True},
        )
        with pytest.raises(HTTPException) as exc:
            verify_pubsub_push(self._request({"authorization": "Bearer signed-but-wrong-identity"}))
        assert exc.value.status_code == 403

    def test_accepts_expected_service_account(self, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
        monkeypatch.setattr(config, "PUBSUB_SERVICE_ACCOUNT", "push@my-project.iam.gserviceaccount.com")
        monkeypatch.setattr(
            security.id_token, "verify_oauth2_token",
            lambda *a, **k: {"email": "push@my-project.iam.gserviceaccount.com", "email_verified": True},
        )
        verify_pubsub_push(self._request({"authorization": "Bearer valid"}))  # must not raise
