"""Endpoint tests: auth boundaries and the review/rule-learning workflow."""

from app import config
from tests.conftest import AUTH_HEADERS


def seed_wallet(fake_db, **overrides):
    doc = {"current_balance": 5000, "transactions": [], "owed_by": {}}
    doc.update(overrides)
    fake_db.store["cfo_state/wallet"] = doc
    return doc


def review_transaction(txn_id="tx-review-1", vpa="ramesh.k@ybl"):
    return {
        "id": txn_id,
        "timestamp": "2026-07-01T10:00:00+05:30",
        "action_taken": "BANK_DEBIT",
        "approved_amount": 340,
        "merchant": "RAMESH KUMAR",
        "payee_raw": "RAMESH KUMAR",
        "payee_normalized": "ramesh kumar",
        "merchant_normalized": "ramesh kumar",
        "upi_vpa": vpa,
        "direction": "OUTBOUND",
        "category": "Unknown",
        "needs_review": True,
        "classification_source": "review",
        "remaining_balance": 4660,
    }


class TestPublicEndpoints:
    def test_healthz(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_public_profile_has_no_finance_data(self, client, fake_db):
        seed_wallet(fake_db, current_balance=1234)
        body = client.get("/public-profile").json()
        assert "1234" not in str(body)
        assert body["title"] == "AgentCFO"


class TestPrivateAccessControl:
    def test_cfo_state_requires_token(self, client, fake_db):
        seed_wallet(fake_db)
        assert client.get("/cfo-state").status_code == 401

    def test_cfo_state_rejects_wrong_token(self, client, fake_db):
        seed_wallet(fake_db)
        response = client.get("/cfo-state", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401

    def test_cfo_state_accepts_bearer_token(self, client, fake_db):
        seed_wallet(fake_db, current_balance=4200)
        response = client.get("/cfo-state", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["current_balance"] == 4200
        assert body["days_left"] == config.RUNWAY_HORIZON_DAYS

    def test_cfo_state_accepts_x_cfo_token_header(self, client, fake_db):
        seed_wallet(fake_db)
        assert client.get("/cfo-state", headers={"X-CFO-Token": "test-token"}).status_code == 200

    def test_query_param_token_is_not_accepted_server_side(self, client, fake_db):
        # Tokens in URLs leak into access logs; only headers/cookies work.
        seed_wallet(fake_db)
        assert client.get("/cfo-state?token=test-token").status_code == 401

    def test_auth_session_sets_cookie(self, client):
        response = client.post("/auth/session", json={"token": "test-token"})
        assert response.status_code == 200
        assert "cfo_access_token" in response.cookies

    def test_auth_session_rejects_bad_token(self, client):
        assert client.post("/auth/session", json={"token": "nope"}).status_code == 401

    def test_mutating_endpoints_require_token(self, client, fake_db):
        seed_wallet(fake_db)
        assert client.post("/cfo-reset").status_code == 401
        assert client.post("/gmail-watch").status_code == 401
        assert client.get("/gmail-status").status_code == 401
        assert client.post("/merchant-rules", json={"pattern": "x", "category": "Food"}).status_code == 401


class TestWebhookVerification:
    def test_webhook_ignores_empty_envelope_when_unverified(self, client):
        response = client.post("/gmail-webhook", json={"message": {}})
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    def test_webhook_requires_oidc_when_audience_configured(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBSUB_AUDIENCE", "https://example.com/gmail-webhook")
        response = client.post("/gmail-webhook", json={"message": {}})
        assert response.status_code == 401


class TestReviewWorkflow:
    def test_review_queue_lists_ambiguous_debits(self, client, fake_db):
        seed_wallet(fake_db, transactions=[review_transaction()])
        body = client.get("/transactions/review", headers=AUTH_HEADERS).json()
        assert len(body["transactions"]) == 1
        assert body["transactions"][0]["id"] == "tx-review-1"

    def test_correction_learns_rule_and_reclassifies_siblings(self, client, fake_db):
        seed_wallet(fake_db, transactions=[
            review_transaction("tx-a"),
            review_transaction("tx-b"),
        ])
        response = client.post(
            "/transactions/tx-a/classify",
            json={"category": "Food", "subcategory": "Street food", "always_apply": True},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["transaction"]["category"] == "Food"
        assert body["merchant_rule"]["pattern"] == "ramesh.k@ybl"
        assert body["reclassified"] == 1  # the sibling got fixed too

        # The learned rule and both corrections persisted.
        wallet = fake_db.store["cfo_state/wallet"]
        assert all(tx["category"] == "Food" for tx in wallet["transactions"])
        assert all(not tx["needs_review"] for tx in wallet["transactions"])
        assert wallet["merchant_rules"][0]["category"] == "Food"

    def test_correction_unknown_transaction_404s(self, client, fake_db):
        seed_wallet(fake_db)
        response = client.post(
            "/transactions/nope/classify",
            json={"category": "Food"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 404

    def test_correction_rejects_invalid_category(self, client, fake_db):
        seed_wallet(fake_db, transactions=[review_transaction()])
        response = client.post(
            "/transactions/tx-review-1/classify",
            json={"category": "Gambling"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 400


class TestMerchantRules:
    def test_create_rule_and_list_it(self, client, fake_db):
        seed_wallet(fake_db)
        create = client.post(
            "/merchant-rules",
            json={"pattern": "chaiwala@oksbi", "category": "Food"},
            headers=AUTH_HEADERS,
        )
        assert create.status_code == 200
        listed = client.get("/merchant-rules", headers=AUTH_HEADERS).json()
        assert listed["merchant_rules"][0]["pattern"] == "chaiwala@oksbi"

    def test_create_rule_rejects_bad_category(self, client, fake_db):
        seed_wallet(fake_db)
        response = client.post(
            "/merchant-rules",
            json={"pattern": "x@ybl", "category": "Nope"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 400


class TestCfoCheck:
    def test_returns_500_when_llm_not_configured(self, client, fake_db):
        seed_wallet(fake_db)
        response = client.post("/cfo-check", json={"expense_text": "coffee for 300"}, headers=AUTH_HEADERS)
        assert response.status_code == 500

    def test_rejects_empty_prompt(self, client, fake_db):
        seed_wallet(fake_db)
        response = client.post("/cfo-check", json={"expense_text": "  "}, headers=AUTH_HEADERS)
        assert response.status_code == 400

    def test_applies_decision_transactionally(self, client, fake_db, monkeypatch):
        from app import llm

        seed_wallet(fake_db, current_balance=5000)
        monkeypatch.setattr(llm, "get_client", lambda: object())
        monkeypatch.setattr(llm, "run_agent_decision", lambda prompt, system: {
            "action_taken": "APPROVE_INTENT",
            "expense_deducted": 300,
            "funds_added": 0,
            "new_target_balance": -1,
            "person_name": "",
            "message": "Approved.",
        })
        response = client.post("/cfo-check", json={"expense_text": "coffee for 300"}, headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["current_balance"] == 4700
        assert body["action_taken"] == "APPROVE_INTENT"
        assert fake_db.store["cfo_state/wallet"]["current_balance"] == 4700
        # The transaction was mirrored to the append-only ledger.
        assert len(fake_db.docs_in("cfo_ledger")) == 1


class TestReset:
    def test_reset_clears_ledger_but_keeps_learned_rules(self, client, fake_db):
        seed_wallet(
            fake_db,
            current_balance=100,
            transactions=[review_transaction()],
            merchant_rules=[{
                "pattern": "chaiwala@oksbi", "category": "Food", "source": "learned",
            }],
        )
        response = client.post("/cfo-reset", headers=AUTH_HEADERS)
        assert response.status_code == 200
        wallet = fake_db.store["cfo_state/wallet"]
        assert wallet["current_balance"] == config.DEFAULT_BALANCE
        assert wallet["transactions"] == []
        assert wallet["merchant_rules"][0]["pattern"] == "chaiwala@oksbi"
