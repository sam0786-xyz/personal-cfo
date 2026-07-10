"""Tests for the deterministic CFO decision math (app.agent)."""

from app.agent import apply_agent_decision, build_agent_prompt


def make_state(balance=5000, owed_by=None, synthetic=None):
    return {
        "current_balance": balance,
        "owed_by": owed_by or {},
        "transactions": [],
        "synthetic_assets": synthetic or {"axis_edge_points": 0, "github_credits_usd": 0},
    }


def decision(action, expense=0, funds=0, target=-1, person="", message="ok"):
    return {
        "action_taken": action,
        "expense_deducted": expense,
        "funds_added": funds,
        "new_target_balance": target,
        "person_name": person,
        "message": message,
    }


class TestSpending:
    def test_approve_intent_deducts(self):
        state = make_state(5000)
        result = apply_agent_decision(state, decision("APPROVE_INTENT", expense=300), "coffee", 30)
        assert state["current_balance"] == 4700
        assert result["current_balance"] == 4700
        assert result["daily_budget_cap"] == round(4700 / 30)
        assert state["transactions"][0]["approved_amount"] == 300

    def test_reject_intent_deducts_nothing(self):
        state = make_state(5000)
        apply_agent_decision(state, decision("REJECT_INTENT", expense=300), "junk", 30)
        assert state["current_balance"] == 5000

    def test_query_status_changes_nothing(self):
        state = make_state(5000, owed_by={"Faizan": 200})
        result = apply_agent_decision(state, decision("QUERY_STATUS"), "balance?", 30)
        assert state["current_balance"] == 5000
        assert result["total_owed"] == 200

    def test_overdraft_protection_rejects(self):
        state = make_state(100)
        result = apply_agent_decision(state, decision("APPROVE_INTENT", expense=5000), "laptop", 30)
        assert state["current_balance"] == 100
        assert result["action_taken"] == "REJECT_INTENT"
        assert "REJECTED" in result["message"]

    def test_negative_amounts_are_sanitized(self):
        state = make_state(5000)
        apply_agent_decision(state, decision("APPROVE_INTENT", expense=-300, funds=-100), "weird", 30)
        assert state["current_balance"] == 5000


class TestFundsAndBalance:
    def test_add_funds(self):
        state = make_state(5000)
        apply_agent_decision(state, decision("ADD_FUNDS", funds=1500), "got paid", 30)
        assert state["current_balance"] == 6500

    def test_set_exact_balance(self):
        state = make_state(5000)
        result = apply_agent_decision(state, decision("SET_EXACT_BALANCE", target=8000), "balance is now 8000", 30)
        assert state["current_balance"] == 8000
        assert result["funds_added"] == 3000  # implied delta is surfaced

    def test_set_exact_balance_downward(self):
        state = make_state(5000)
        apply_agent_decision(state, decision("SET_EXACT_BALANCE", target=2000), "balance is now 2000", 30)
        assert state["current_balance"] == 2000


class TestDebtLedger:
    def test_lend_money_tracks_debt(self):
        state = make_state(5000)
        result = apply_agent_decision(state, decision("LEND_MONEY", expense=500, person="faizan"), "lent 500", 30)
        assert state["current_balance"] == 4500
        assert state["owed_by"] == {"Faizan": 500}
        assert result["total_owed"] == 500

    def test_lend_without_name_falls_back(self):
        state = make_state(5000)
        apply_agent_decision(state, decision("LEND_MONEY", expense=500), "lent 500", 30)
        assert state["owed_by"] == {"Someone": 500}

    def test_debt_collected_clears_ledger(self):
        state = make_state(4500, owed_by={"Faizan": 500})
        apply_agent_decision(state, decision("DEBT_COLLECTED", funds=500, person="Faizan"), "faizan paid back", 30)
        assert state["current_balance"] == 5000
        assert state["owed_by"] == {}  # fully repaid entries are dropped

    def test_partial_debt_collection(self):
        state = make_state(4500, owed_by={"Faizan": 500})
        apply_agent_decision(state, decision("DEBT_COLLECTED", funds=200, person="Faizan"), "partial", 30)
        assert state["owed_by"] == {"Faizan": 300}


class TestSyntheticAssets:
    def test_simulate_contract_never_touches_balance(self):
        state = make_state(5000)
        result = apply_agent_decision(
            state, decision("SIMULATE_CONTRACT", funds=5000, person="AED"), "simulate 5000 AED", 30
        )
        assert state["current_balance"] == 5000
        assert result["funds_added"] == 0
        assert "Contract Simulation" in result["message"]
        assert "Skydo" in result["message"]

    def test_update_synthetic_gain_never_touches_cash(self):
        state = make_state(5000, synthetic={"axis_edge_points": 100, "github_credits_usd": 0})
        result = apply_agent_decision(
            state, decision("UPDATE_SYNTHETIC", funds=500, person="axis edge"), "got 500 points", 30
        )
        assert state["current_balance"] == 5000
        assert state["synthetic_assets"]["axis_edge_points"] == 600
        assert result["expense_deducted"] == 0

    def test_update_synthetic_spend_never_touches_cash(self):
        state = make_state(5000, synthetic={"axis_edge_points": 0, "github_credits_usd": 50})
        apply_agent_decision(
            state, decision("UPDATE_SYNTHETIC", expense=10, person="github credits"), "used 10 credits", 30
        )
        assert state["current_balance"] == 5000
        assert state["synthetic_assets"]["github_credits_usd"] == 40

    def test_reroute_to_synthetic_blocks_cash_burn(self):
        state = make_state(5000, synthetic={"axis_edge_points": 25000, "github_credits_usd": 0})
        result = apply_agent_decision(
            state, decision("REROUTE_TO_SYNTHETIC", expense=3000), "book a flight", 30
        )
        assert state["current_balance"] == 5000
        assert "25000" in result["message"]


class TestPrompt:
    def test_prompt_includes_state(self):
        state = make_state(4321, owed_by={"Faizan": 200}, synthetic={"axis_edge_points": 10})
        prompt = build_agent_prompt(state, 12)
        assert "₹4321" in prompt
        assert "Faizan owes ₹200" in prompt
        assert "axis_edge_points: 10" in prompt
        assert "12 days" in prompt

    def test_prompt_handles_empty_state(self):
        prompt = build_agent_prompt(make_state(), 30)
        assert "Nobody owes him money." in prompt
