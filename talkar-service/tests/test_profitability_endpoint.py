import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from db.models import TalkarAdmin
from db.session import get_db
from routers.admin import get_profitability, get_current_admin, OPENAI_RATES


@pytest.mark.asyncio
async def test_get_profitability_cost_assumptions_and_rates():
    """Verify get_profitability returns 200 payload with all cost assumptions without NameError."""
    assert "gpt-4o-mini" in OPENAI_RATES
    assert "gpt-4o" in OPENAI_RATES

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[]),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=0),
        )
    )

    for period in ["today", "week", "month", "all"]:
        res = await get_profitability(period=period, db=mock_db, current_admin=MagicMock())
        assert res["period"] == period
        assert "summary" in res
        assert "cost_assumptions" in res
        assumptions = res["cost_assumptions"]
        assert "openai_rates" in assumptions
        assert assumptions["openai_rates"]["gpt-4o-mini"]["input"] == 0.15
        assert assumptions["openai_rates"]["gpt-4o-mini"]["output"] == 0.60


def test_profitability_http_cors_and_status():
    """Verify end-to-end HTTP request with Origin header receives 200 OK and CORS header."""
    client = TestClient(app, raise_server_exceptions=False)
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[]),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=0),
        )
    )

    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        for period in ["today", "week", "month", "all"]:
            resp = client.get(
                f"/admin/profitability?period={period}",
                headers={"Origin": "https://admin.talkar.in"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "https://admin.talkar.in"
            data = resp.json()
            assert data["period"] == period
            assert "cost_assumptions" in data
            assert "openai_rates" in data["cost_assumptions"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_profitability_mixed_model_vendor_aggregation():
    """
    Validates that when calls have mixed model configurations (e.g. Deepgram + Sarvam STT,
    Smallest AI + Cartesia TTS, OpenAI + Claude LLM), get_profitability accurately attributes
    and aggregates vendor costs in stt_vendors, tts_vendors, and llm_vendors without cross-contamination.
    """
    from types import SimpleNamespace
    from db.models import Customer, Subscription

    mock_db = MagicMock()

    # 1. Mock Customer
    mock_customer = Customer(
        id=10,
        company_name="Tru-Herb Biotech",
        contact_email="obs@tru-herb.com",
        status="active",
        is_test_account=False,
        report_settings={},
    )
    # 2. Mock Subscription
    mock_sub = Subscription(
        customer_id=10,
        plan="growth",
        per_minute_rate_paise=600,
    )
    # 3. Mock 2 Call Logs for Customer 10
    call_1 = SimpleNamespace(
        id=101,
        customer_id=10,
        dograh_run_id=5001,
        duration_seconds=60,
        cost_to_customer_paise=600,
        plan="growth",
        tts_provider="smallest_ai",
        call_status="completed",
    )
    call_2 = SimpleNamespace(
        id=102,
        customer_id=10,
        dograh_run_id=5002,
        duration_seconds=120,
        cost_to_customer_paise=1200,
        plan="growth",
        tts_provider="cartesia",
        call_status="completed",
    )

    # Mock DB queries
    def execute_mock(stmt, *args, **kwargs):
        stmt_str = str(stmt).lower()
        res_mock = MagicMock()
        if "from call_logs" in stmt_str or "call_logs" in stmt_str:
            res_mock.all.return_value = [call_1, call_2]
            res_mock.scalars.return_value.all.return_value = [call_1, call_2]
        elif "from customers" in stmt_str:
            res_mock.scalars.return_value.all.return_value = [mock_customer]
        elif "from subscriptions" in stmt_str:
            res_mock.scalars.return_value.all.return_value = [mock_sub]
        elif "from global_platform_settings" in stmt_str:
            res_mock.scalar_one_or_none.return_value = None
        elif "wallet_transactions" in stmt_str:
            res_mock.scalar.return_value = 0
        else:
            res_mock.scalars.return_value.all.return_value = []
            res_mock.all.return_value = []
            res_mock.scalar_one_or_none.return_value = None
            res_mock.scalar.return_value = 0
        return res_mock

    mock_db.execute = AsyncMock(side_effect=execute_mock)

    # Mock Dograh Session fetching usage_info for runs 5001 and 5002
    mock_run_1_usage = {
        "stt": {"DeepgramSTTService#0": {"audio_seconds": 60.0}},
        "tts": {"SmallestAITTSService#0": {"characters": 800}},
        "llm": {"assistant|||gpt-4o-mini": {"prompt_tokens": 1000, "completion_tokens": 100}},
    }
    mock_run_2_usage = {
        "stt": {"sarvam|||saaras": {"audio_seconds": 120.0}},
        "tts": {"CartesiaTTSService#0": {"characters": 1500}},
        "llm": {"assistant|||claude-3-5-sonnet": {"prompt_tokens": 1500, "completion_tokens": 150}},
    }

    mock_ddb = AsyncMock()
    mock_ddb_rows = [
        SimpleNamespace(id=5001, usage_info=mock_run_1_usage),
        SimpleNamespace(id=5002, usage_info=mock_run_2_usage),
    ]
    mock_ddb.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=mock_ddb_rows)))

    with patch("services.dograh_client.DograhSessionLocal") as mock_dograh_session_cls:
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_ddb)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_dograh_session_cls.return_value = session_ctx

        res = await get_profitability(period="month", db=mock_db, current_admin=MagicMock())

    assert len(res["customers"]) == 1
    cust = res["customers"][0]
    assert cust["customer_id"] == 10
    assert cust["calls"] == 2
    assert cust["total_minutes"] == 3.0

    bdown = cust["breakdown"]
    # STT Vendors must contain Deepgram and Sarvam (NEVER Smallest AI)
    assert "deepgram" in bdown["stt_vendors"]
    assert "sarvam" in bdown["stt_vendors"]
    assert "smallest_ai" not in bdown["stt_vendors"]
    assert bdown["stt_vendors"]["deepgram"] > 0
    assert bdown["stt_vendors"]["sarvam"] > 0

    # TTS Vendors must contain Smallest AI and Cartesia
    assert "smallest_ai" in bdown["tts_vendors"]
    assert "cartesia" in bdown["tts_vendors"]
    assert bdown["tts_vendors"]["smallest_ai"] > 0
    assert bdown["tts_vendors"]["cartesia"] > 0

    # LLM Vendors must contain OpenAI and Anthropic
    assert "openai" in bdown["llm_vendors"]
    assert "anthropic" in bdown["llm_vendors"]
    assert bdown["llm_vendors"]["openai"] > 0
    assert bdown["llm_vendors"]["anthropic"] > 0

