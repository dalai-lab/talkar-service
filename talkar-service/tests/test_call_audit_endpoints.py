"""
Smoke and Integration Tests for Call Cost Audit REST API Endpoints.

Specification Reference: /Users/arnav/Internship/call_cost_audit_master_spec.md
Deliverable Test Requirements (Phase 3):
  - Endpoint smoke test using TestClient: Query call ID 8052 and verify JSON payload matches the spec contract in Section 5.1.
  - Test GET /admin/call-audit/customers
  - Test GET /admin/call-audit/calls
  - Test GET /admin/call-audit/run/{dograh_run_id}
  - Test POST /admin/call-audit/simulate
  - Test RBAC Security (unauthenticated access rejected)
"""

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

# Ensure root directory is on sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_current_dir)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from main import app
from db.models import Customer, CallLog, Subscription, TalkarAdmin
from db.session import get_db
from services.admin_auth import get_current_admin


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def mock_admin():
    admin = MagicMock(spec=TalkarAdmin)
    admin.id = 1
    admin.email = "admin@talkar.com"
    admin.role = "admin"
    return admin


@pytest.fixture
def mock_call_8052_data():
    """
    Standard Benchmark Call 8052 (Samsung India) as specified in Section 5.1:
      - Duration: 76 seconds (2 billed minutes)
      - Revenue: ₹7.00 (700 paise)
      - STT: Deepgram Nova-3 (42.5s)
      - Conversational LLM: Sarvam-105B (1240 prompt, 72 comp, 400 cached)
      - Extraction LLM: GPT-4.1-mini (820 prompt, 35 comp)
      - TTS: Smallest AI Lightning V3.1 (612 chars)
    """
    customer = Customer(
        id=14,
        company_name="Samsung India",
        contact_name="Samsung Admin",
        contact_email="admin@samsung.in",
        status="approved",
        is_test_account=False,
        report_settings={},
    )
    call_log = CallLog(
        id=101,
        dograh_run_id=8052,
        customer_id=14,
        duration_seconds=76,
        cost_to_customer_paise=700,
        caller_number="+919876543210",
        call_type="outbound",
        plan="growth",
        tts_provider="smallest_ai",
    )
    sub = Subscription(
        id=1,
        customer_id=14,
        plan="growth",
        status="active",
        per_minute_rate_paise=350,
    )
    workflow_run = {
        "id": 8052,
        "state": "completed",
        "mode": "outbound",
        "usage_info": {
            "stt": {
                "deepgram_nova_3": {"audio_seconds": 42.5}
            },
            "llm": {
                "sarvam|||sarvam-105b": {
                    "prompt_tokens": 1240,
                    "completion_tokens": 72,
                    "cache_read_input_tokens": 400,
                },
                "variable_extraction:extraction|||gpt-4.1-mini": {
                    "prompt_tokens": 820,
                    "completion_tokens": 35,
                    "cache_read_input_tokens": 0,
                },
            },
            "tts": {
                "smallest_ai_lightning": {"characters": 612}
            },
        },
    }
    return customer, call_log, sub, workflow_run


# ==============================================================================
# TESTS
# ==============================================================================

def test_query_call_8052_matches_section_5_1_spec_contract(mock_admin, mock_call_8052_data):
    """
    DELIVERABLE TEST:
    Assert exact mathematical agreement against the Section 5.1 JSON contract for Call 8052:
      - Total cost: ₹2.19
      - Profit: ₹4.81
      - Gross Margin %: 68.7%
      - Cost breakdown items exactly match down to the paisa.
    """
    customer, call_log, sub, workflow_run = mock_call_8052_data

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from call_logs" in q_str:
            res.scalar_one_or_none.return_value = call_log
            res.scalars.return_value.all.return_value = [call_log]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = customer
            res.scalars.return_value.all.return_value = [customer]
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = sub
            res.scalars.return_value.all.return_value = [sub]
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_db

    # Mock Dograh Session to return workflow_run
    mock_ddb = MagicMock()
    mock_row = MagicMock()
    mock_row.id = workflow_run["id"]
    mock_row.usage_info = workflow_run["usage_info"]
    mock_row.state = workflow_run["state"]
    mock_row.mode = workflow_run["mode"]
    
    mock_ddb_res = MagicMock()
    mock_ddb_res.fetchone.return_value = mock_row
    mock_ddb.execute = AsyncMock(return_value=mock_ddb_res)
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock()

    try:
        with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
             patch("services.dograh_client._has_dograh_db", return_value=True):
            
            client = TestClient(app)
            response = client.get("/admin/call-audit/run/8052")
            
            assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}: {response.text}"
            data = response.json()

            # Spec Section 5.1 Contract Assertions:
            assert data["dograh_run_id"] == 8052
            assert data["customer_id"] == 14
            assert data["company_name"] == "Samsung India"
            assert data["call_duration_seconds"] == 76
            assert data["billed_minutes"] == 2
            assert data["revenue_inr"] == 7.00
            assert data["effective_rate_per_min_inr"] == 3.50
            assert data["exchange_rate_used"] == 88.00

            # Cost Breakdown Assertions:
            cb = data["cost_breakdown"]
            # Plivo: 2 mins @ ₹0.45 = ₹0.90
            assert cb["plivo"]["minutes"] == 2
            assert cb["plivo"]["rate_inr"] == 0.45
            assert cb["plivo"]["cost_inr"] == 0.90

            # STT: Deepgram Nova-3 42.5s @ $0.0048/min @ ₹88 = ₹0.30
            assert cb["stt"]["provider"] == "deepgram"
            assert cb["stt"]["model"] == "nova-3"
            assert cb["stt"]["seconds"] == 42.5
            assert cb["stt"]["rate_usd_min"] == 0.0048
            assert cb["stt"]["cost_inr"] == 0.30

            # LLM Conversation: Sarvam-105B = ₹0.02
            assert cb["llm_conversation"]["provider"] == "sarvam"
            assert cb["llm_conversation"]["model"] == "sarvam-105b"
            assert cb["llm_conversation"]["prompt_tokens"] == 1240
            assert cb["llm_conversation"]["completion_tokens"] == 72
            assert cb["llm_conversation"]["cached_tokens"] == 400
            assert cb["llm_conversation"]["cost_inr"] == 0.02

            # LLM Extraction: GPT-4.1-mini = ₹0.03
            assert cb["llm_extraction"]["provider"] == "openai"
            assert cb["llm_extraction"]["model"] == "gpt-4.1-mini"
            assert cb["llm_extraction"]["prompt_tokens"] == 820
            assert cb["llm_extraction"]["completion_tokens"] == 35
            assert cb["llm_extraction"]["cost_inr"] == 0.03

            # TTS: Smallest AI Lightning V3.1 612 chars = ₹0.94
            assert cb["tts"]["provider"] == "smallest_ai"
            assert cb["tts"]["model"] == "lightning-v3.1"
            assert cb["tts"]["characters"] == 612
            assert cb["tts"]["rate_usd_per_10k"] == 0.175
            assert cb["tts"]["cost_inr"] == 0.94

            # Aggregated Metrics:
            assert data["total_cost_inr"] == 2.19
            assert data["cost_per_minute_inr"] == 1.73
            assert data["profit_inr"] == 4.81
            assert data["margin_pct"] == 68.7
            assert data["margin_status"] == "healthy"
    finally:
        app.dependency_overrides.clear()


def test_simulate_endpoint_what_if_analysis(mock_admin, mock_call_8052_data):
    """
    Tests POST /admin/call-audit/simulate:
    Simulates switching TTS to ElevenLabs Multilingual on Call 8052 and verifies margin delta.
    """
    customer, call_log, sub, workflow_run = mock_call_8052_data

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from call_logs" in q_str:
            res.scalar_one_or_none.return_value = call_log
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = customer
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_db

    mock_ddb = MagicMock()
    mock_row = MagicMock()
    mock_row.id = workflow_run["id"]
    mock_row.usage_info = workflow_run["usage_info"]
    mock_row.state = workflow_run["state"]
    mock_row.mode = workflow_run["mode"]
    mock_ddb_res = MagicMock()
    mock_ddb_res.fetchone.return_value = mock_row
    mock_ddb.execute = AsyncMock(return_value=mock_ddb_res)
    mock_ddb.__aenter__ = AsyncMock(return_value=mock_ddb)
    mock_ddb.__aexit__ = AsyncMock()

    try:
        with patch("services.dograh_client.DograhSessionLocal", return_value=mock_ddb), \
             patch("services.dograh_client._has_dograh_db", return_value=True):
            
            client = TestClient(app)
            payload = {
                "dograh_run_id": 8052,
                "override_tts_provider": "elevenlabs_multilingual",
            }
            response = client.post("/admin/call-audit/simulate", json=payload)
            assert response.status_code == 200, f"Expected 200, got: {response.text}"
            data = response.json()

            assert data["dograh_run_id"] == 8052
            # Baseline is ~2.19
            assert pytest.approx(data["baseline"]["total_cost_inr"], abs=0.01) == 2.19
            # Simulated with ElevenLabs Multilingual:
            # TTS rises to 612 * $0.080/1k * 88 = ₹4.308 (from ₹0.94)
            # Total rises to ~₹5.56
            assert data["simulated"]["cost_breakdown"]["tts"]["provider"] == "elevenlabs"
            assert data["simulated"]["total_cost_inr"] > data["baseline"]["total_cost_inr"]
            assert data["delta"]["total_cost_delta_inr"] > 0
            assert data["delta"]["profit_delta_inr"] < 0
            assert data["delta"]["margin_pct_delta"] < 0
    finally:
        app.dependency_overrides.clear()


def test_get_call_audit_customers_endpoint(mock_admin, mock_call_8052_data):
    """
    Tests GET /admin/call-audit/customers pagination and searchable listing.
    """
    customer, call_log, sub, _ = mock_call_8052_data

    mock_db = MagicMock()
    mock_row = MagicMock()
    mock_row.id = 14
    mock_row.company_name = "Samsung India"
    mock_row.contact_email = "admin@samsung.in"
    mock_row.contact_name = "Admin"
    mock_row.status = "approved"
    mock_row.subscription_plan = "growth"
    mock_row.total_calls = 5
    mock_row.total_duration_seconds = 380
    mock_row.total_revenue_paise = 3500
    mock_row.report_settings = {"currency": "INR"}

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "group by" in q_str:
            res.all.return_value = [mock_row]
        else:
            res.scalar.return_value = 1
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        client = TestClient(app)
        response = client.get("/admin/call-audit/customers?search=Samsung&page=1&limit=20")
        assert response.status_code == 200, f"Expected 200, got: {response.text}"
        data = response.json()

        assert data["total"] == 1
        assert len(data["customers"]) == 1
        c = data["customers"][0]
        assert c["id"] == 14
        assert c["company_name"] == "Samsung India"
        assert c["total_calls"] == 5
        assert c["total_revenue_inr"] == 35.0
    finally:
        app.dependency_overrides.clear()


def test_get_call_audit_calls_endpoint(mock_admin, mock_call_8052_data):
    """
    Tests GET /admin/call-audit/calls list for customer_id=14.
    """
    customer, call_log, sub, _ = mock_call_8052_data

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "count" in q_str:
            res.scalar.return_value = 1
        else:
            res.scalars.return_value.all.return_value = [call_log]
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_db

    try:
        client = TestClient(app)
        response = client.get("/admin/call-audit/calls?customer_id=14&page=1&limit=20")
        assert response.status_code == 200, f"Expected 200, got: {response.text}"
        data = response.json()

        assert data["total"] == 1
        assert len(data["calls"]) == 1
        c = data["calls"][0]
        assert c["dograh_run_id"] == 8052
        assert c["duration_seconds"] == 76
        assert c["revenue_inr"] == 7.00
        assert c["status"] == "completed"
    finally:
        app.dependency_overrides.clear()


def test_unauthenticated_request_rejected_with_401():
    """
    EC-10 & Section 9: Access without valid admin authentication must be rejected with 401/403.
    """
    app.dependency_overrides.clear()
    client = TestClient(app)

    res = client.get("/admin/call-audit/customers")
    assert res.status_code in [401, 403]

    res = client.get("/admin/call-audit/run/8052")
    assert res.status_code in [401, 403]

    res = client.post("/admin/call-audit/simulate", json={"dograh_run_id": 8052})
    assert res.status_code in [401, 403]
