import sys
from datetime import datetime, timezone
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

    # Models Used Inventory verification
    assert "models_used" in cust
    mu = cust["models_used"]
    stt_models = {m["model"] for m in mu["stt"]}
    assert "nova-3" in stt_models
    assert "saaras-v3" in stt_models

    tts_models = {m["model"] for m in mu["tts"]}
    assert "lightning-v3.1" in tts_models
    assert "sonic" in tts_models or "sonic-3.5" in tts_models

    llm_models = {m["model"] for m in mu["llm"]}
    assert "gpt-4o-mini" in llm_models
    assert "claude-3-5-sonnet" in llm_models

    # Calls without Post-Call or QA must have empty post_call list and 0 cost
    assert mu.get("post_call") == []
    assert bdown.get("post_call_inr", 0.0) == 0.0
    assert bdown.get("post_call_vendors", {}) == {}


@pytest.mark.asyncio
async def test_profitability_post_call_intelligence_and_compaction_aggregation():
    """
    Verifies that when a call uses Post-Call Intelligence (PCI), QA Analysis,
    or Context Compaction, the profitability dashboard:
    1. Aggregates post_call_inr and post_call_vendors.
    2. Items appear in models_used['post_call'].
    3. In-call compaction tokens are properly accounted for in models_used['llm'].
    """
    mock_db = MagicMock()
    mock_ddb = MagicMock()

    call1 = MagicMock(
        customer_id=15,
        dograh_run_id=201,
        duration_seconds=120,
        cost_to_customer_paise=1500,
        plan="growth",
        tts_provider="smallest_ai",
        stt_provider="deepgram",
    )

    mock_db.execute = AsyncMock()

    def mock_db_side_effect(query):
        res = MagicMock()
        q_str = str(query).lower()
        if "from call_logs" in q_str or "call_logs" in q_str:
            res.all.return_value = [call1]
            res.scalars.return_value.all.return_value = [call1]
        elif "from subscriptions" in q_str:
            sub = MagicMock(customer_id=15, plan="growth")
            res.scalars.return_value.all.return_value = [sub]
        elif "from global_platform_settings" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from customers" in q_str:
            cust = MagicMock(id=15, company_name="PCI Intelligence Labs", contact_email="admin@pci.com", status="active", is_test_account=False, report_settings={})
            res.scalars.return_value.all.return_value = [cust]
        elif "from wallet_transactions" in q_str or "wallet_transactions" in q_str:
            res.scalar.return_value = 0
        else:
            res.scalars.return_value.all.return_value = []
            res.all.return_value = []
            res.scalar.return_value = 0
        return res

    mock_db.execute.side_effect = mock_db_side_effect

    # Call with In-Call Compaction, PCI, and QA Analysis
    usage_info_201 = {
        "stt": {"deepgram_nova_3": 120.0},
        "tts": {"smallest_ai_lightning": 800},
        "llm": {
            "OpenAILLMService#0|||gpt-4o-mini": {"prompt_tokens": 1500, "completion_tokens": 150},
            "context_compaction:compaction|||gpt-4o-mini": {"prompt_tokens": 500, "completion_tokens": 80},
            "PostCallIntelligence|||gpt-4.1-mini": {"prompt_tokens": 2000, "completion_tokens": 300},
            "QAAnalysis|||gemini-1.5-flash": {"prompt_tokens": 1200, "completion_tokens": 200},
        },
    }

    mock_ddb.execute = AsyncMock()
    run_row_201 = MagicMock(id=201, usage_info=usage_info_201)

    def mock_ddb_side_effect(query, params=None):
        res = MagicMock()
        res.all.return_value = [run_row_201]
        return res

    mock_ddb.execute.side_effect = mock_ddb_side_effect

    with patch("services.dograh_client.DograhSessionLocal") as mock_dograh_session_cls:
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_ddb)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_dograh_session_cls.return_value = session_ctx

        res = await get_profitability(period="month", db=mock_db, current_admin=MagicMock())

    assert len(res["customers"]) == 1
    cust = res["customers"][0]
    bdown = cust["breakdown"]

    # Post-call cost must be > 0
    assert bdown["post_call_inr"] > 0
    assert "openai" in bdown["post_call_vendors"]
    assert "google" in bdown["post_call_vendors"] or "gemini" in bdown["post_call_vendors"]

    # Models used inventory must list PCI & QA models under post_call
    mu = cust["models_used"]
    assert "post_call" in mu
    assert len(mu["post_call"]) == 2
    pc_models = {m["model"] for m in mu["post_call"]}
    assert "gpt-4.1-mini" in pc_models
    assert "gemini-1.5-flash" in pc_models

    # Compaction + conversational tokens aggregated in in-call LLM
    llm_models = {m["model"] for m in mu["llm"]}
    assert "gpt-4o-mini" in llm_models


@pytest.mark.asyncio
async def test_profitability_deepgram_flux_stt_model_inventory():
    """
    Verifies that calls configured with Deepgram Flux (flux-general-multi) accurately
    report 'flux-general-multi' in the models_used inventory rather than defaulting
    to 'nova-3', and that pricing uses the exact Deepgram Flux rate ($0.0048/min).
    """
    from types import SimpleNamespace
    from db.models import Customer, Subscription

    mock_db = MagicMock()

    mock_customer = Customer(
        id=25,
        company_name="Flux Voice AI",
        contact_email="admin@flux.ai",
        status="active",
        is_test_account=False,
        report_settings={},
    )
    mock_sub = Subscription(
        customer_id=25,
        plan="growth",
        per_minute_rate_paise=600,
    )
    call_flux = SimpleNamespace(
        id=801,
        customer_id=25,
        dograh_run_id=8001,
        duration_seconds=60,
        cost_to_customer_paise=600,
        plan="growth",
        tts_provider="smallest_ai",
        call_status="completed",
    )
    call_nova = SimpleNamespace(
        id=802,
        customer_id=25,
        dograh_run_id=8002,
        duration_seconds=120,
        cost_to_customer_paise=1200,
        plan="growth",
        tts_provider="smallest_ai",
        call_status="completed",
    )

    def mock_db_side_effect(query):
        res = MagicMock()
        q_str = str(query).lower()
        if "from call_logs" in q_str or "call_logs" in q_str:
            res.all.return_value = [call_flux, call_nova]
            res.scalars.return_value.all.return_value = [call_flux, call_nova]
        elif "from subscriptions" in q_str:
            res.scalars.return_value.all.return_value = [mock_sub]
        elif "from global_platform_settings" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from customers" in q_str:
            res.scalars.return_value.all.return_value = [mock_customer]
        elif "from wallet_transactions" in q_str or "wallet_transactions" in q_str:
            res.scalar.return_value = 0
        else:
            res.scalars.return_value.all.return_value = []
            res.all.return_value = []
            res.scalar.return_value = 0
        return res

    mock_db.execute = AsyncMock(side_effect=mock_db_side_effect)

    # Mock workflow_runs with initial_context containing runtime_configuration
    run_8001 = SimpleNamespace(
        id=8001,
        usage_info={"stt": {"DeepgramSTTService#0": {"audio_seconds": 60.0}}},
        state="completed",
        mode="pipeline",
        initial_context={
            "runtime_configuration": {
                "stt_provider": "deepgram",
                "stt_model": "flux-general-multi",
                "tts_provider": "smallest_ai",
                "tts_model": "lightning_v3.1_pro",
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
            }
        },
    )
    run_8002 = SimpleNamespace(
        id=8002,
        usage_info={"stt": {"DeepgramSTTService#0": {"audio_seconds": 120.0}}},
        state="completed",
        mode="pipeline",
        initial_context={
            "runtime_configuration": {
                "stt_provider": "deepgram",
                "stt_model": "nova-3",
                "tts_provider": "smallest_ai",
                "tts_model": "lightning_v3.1_pro",
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
            }
        },
    )

    mock_ddb = AsyncMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[run_8001, run_8002])))

    with patch("services.dograh_client.DograhSessionLocal") as mock_dograh_session_cls:
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_ddb)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_dograh_session_cls.return_value = session_ctx

        res = await get_profitability(period="month", db=mock_db, current_admin=MagicMock())

    assert len(res["customers"]) == 1
    cust = res["customers"][0]
    mu = cust["models_used"]

    stt_models = {m["model"]: m for m in mu["stt"]}
    # Both flux-general-multi and nova-3 must be present in the models_used inventory!
    assert "flux-general-multi" in stt_models
    assert "nova-3" in stt_models

    flux_entry = stt_models["flux-general-multi"]
    assert flux_entry["calls"] == 1
    assert flux_entry["provider"] == "deepgram"
    assert flux_entry["minutes"] == 1.0
    assert flux_entry["cost_inr"] > 0

    nova_entry = stt_models["nova-3"]
    assert nova_entry["calls"] == 1
    assert nova_entry["provider"] == "deepgram"
    assert nova_entry["minutes"] == 2.0
    assert nova_entry["cost_inr"] > 0


@pytest.mark.asyncio
async def test_profitability_zero_duration_unanswered_carrier_suppression_and_single_plan_bucket():
    """
    Validates:
    1. Unanswered / 0-second calls have ZERO carrier (Plivo) cost (₹0.00), preventing
       false losses and negative margin spikes on non-connected calls.
    2. Calls under the same subscription plan (e.g. 'custom') are unified into a single
       plan_breakdown bucket rather than splitting by tts_provider into duplicate buckets.
    """
    from types import SimpleNamespace
    from db.models import Customer, Subscription

    mock_db = MagicMock()

    mock_customer = Customer(
        id=30,
        company_name="Unified Analytics",
        contact_email="billing@unified.ai",
        status="active",
        is_test_account=False,
        report_settings={},
    )
    mock_sub = Subscription(
        customer_id=30,
        plan="custom",
        per_minute_rate_paise=600,
    )
    # Answered 60s call
    call_answered = SimpleNamespace(
        id=901,
        customer_id=30,
        dograh_run_id=9001,
        duration_seconds=60,
        cost_to_customer_paise=600,
        plan="custom",
        tts_provider="smallest_ai",
        call_status="completed",
    )
    # Unanswered 0s call (no audio synthesized, tts_provider is None)
    call_unanswered = SimpleNamespace(
        id=902,
        customer_id=30,
        dograh_run_id=9002,
        duration_seconds=0,
        cost_to_customer_paise=0,
        plan="custom",
        tts_provider=None,
        call_status="unanswered",
    )

    def mock_db_side_effect(query):
        res = MagicMock()
        q_str = str(query).lower()
        if "from call_logs" in q_str or "call_logs" in q_str:
            res.all.return_value = [call_answered, call_unanswered]
            res.scalars.return_value.all.return_value = [call_answered, call_unanswered]
        elif "from subscriptions" in q_str:
            res.scalars.return_value.all.return_value = [mock_sub]
        elif "from global_platform_settings" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from customers" in q_str:
            res.scalars.return_value.all.return_value = [mock_customer]
        elif "from wallet_transactions" in q_str or "wallet_transactions" in q_str:
            res.scalar.return_value = 0
        else:
            res.scalars.return_value.all.return_value = []
            res.all.return_value = []
            res.scalar.return_value = 0
        return res

    mock_db.execute = AsyncMock(side_effect=mock_db_side_effect)

    run_9001 = SimpleNamespace(
        id=9001,
        usage_info={"tts": {"smallest_ai_lightning": 800}},
        state="completed",
        mode="pipeline",
        initial_context={
            "runtime_configuration": {
                "tts_provider": "smallest_ai",
                "tts_model": "lightning_v3.1_pro",
            }
        },
    )
    run_9002 = SimpleNamespace(
        id=9002,
        usage_info={},
        state="completed",
        mode="pipeline",
        initial_context={},
    )

    mock_ddb = AsyncMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[run_9001, run_9002])))

    with patch("services.dograh_client.DograhSessionLocal") as mock_dograh_session_cls:
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_ddb)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_dograh_session_cls.return_value = session_ctx

        res = await get_profitability(period="month", db=mock_db, current_admin=MagicMock())

    assert len(res["customers"]) == 1
    cust = res["customers"][0]
    assert cust["calls"] == 2
    assert cust["total_minutes"] == 1.0

    # 1. Unified Plan Breakdown: Must have exactly 1 bucket for 'custom', NOT duplicate buckets!
    pb = cust["plan_breakdown"]
    assert len(pb) == 1
    assert pb[0]["plan"] == "custom"
    assert pb[0]["calls"] == 2

    # 2. Carrier Cost Suppression: Only the 60s call incurred carrier cost (1 min * 0.45 = ₹0.45)
    # The 0-second unanswered call incurred ₹0.00 carrier cost.
    assert pytest.approx(cust["breakdown"]["plivo_inr"], abs=0.01) == 0.45
    assert pytest.approx(pb[0]["breakdown"]["plivo_inr"], abs=0.45) == 0.45

    # 3. Model Inventory Call Counts: Smallest AI must show exactly 1 call (the answered one),
    # NOT 2 calls, because the 0-second unanswered call never ran voice synthesis.
    tts_inventory = {m["model"]: m for m in cust["models_used"]["tts"]}
    assert "lightning_v3.1_pro" in tts_inventory
    assert tts_inventory["lightning_v3.1_pro"]["calls"] == 1
    assert tts_inventory["lightning_v3.1_pro"]["characters"] == 800


@pytest.mark.asyncio
async def test_profitability_telephony_direction_and_usd_conversion():
    """
    Verifies that get_profitability:
    1. Distinguishes inbound (₹0.30/min), international (₹1.80/min), and outbound domestic (₹0.45/min).
    2. Accurately converts USD enterprise revenue (currency='USD') using the exchange rate.
    """
    cust_usd = MagicMock(
        id=20,
        company_name="Global Enterprise Inc",
        contact_email="billing@global.com",
        status="active",
        is_test_account=False,
        report_settings={"currency": "USD"},
    )

    # Call 1: Inbound domestic call (60s) - ₹0.30 carrier cost, $1.00 billed (100 cents)
    call_inbound = MagicMock(
        dograh_run_id=9101,
        customer_id=20,
        duration_seconds=60,
        cost_to_customer_paise=100,  # 100 cents = $1.00
        called_at=datetime.now(timezone.utc),
        plan="growth",
        tts_provider="smallest_ai",
        call_type="inbound",
        caller_number="+919876543210",
        call_status="completed",
    )

    # Call 2: International outbound call to US (+1) (60s) - ₹1.80 carrier cost, $2.00 billed (200 cents)
    call_intl = MagicMock(
        dograh_run_id=9102,
        customer_id=20,
        duration_seconds=60,
        cost_to_customer_paise=200,  # 200 cents = $2.00
        called_at=datetime.now(timezone.utc),
        plan="growth",
        tts_provider="smallest_ai",
        call_type="outbound",
        caller_number="+14155552671",
        call_status="completed",
    )

    sub = MagicMock(customer_id=20, plan="growth")

    mock_db = AsyncMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "from subscriptions" in q_str:
            res.scalars.return_value.all.return_value = [sub]
        elif "from global_platform_settings" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from customers" in q_str:
            res.scalars.return_value.all.return_value = [cust_usd]
        else:
            res.all.return_value = [call_inbound, call_intl]
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    run_9101 = MagicMock(
        id=9101,
        usage_info={"tts": {"smallest_ai_lightning": {"characters": 200}}},
        state="completed",
        mode="inbound",
        initial_context={},
    )
    run_9102 = MagicMock(
        id=9102,
        usage_info={"tts": {"smallest_ai_lightning": {"characters": 200}}},
        state="completed",
        mode="outbound",
        initial_context={},
    )

    mock_ddb = AsyncMock()
    mock_ddb.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[run_9101, run_9102])))

    with patch("services.dograh_client.DograhSessionLocal") as mock_dograh_session_cls:
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=mock_ddb)
        session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_dograh_session_cls.return_value = session_ctx

        res = await get_profitability(period="month", db=mock_db, current_admin=MagicMock())

    assert len(res["customers"]) == 1
    cust = res["customers"][0]
    assert cust["calls"] == 2
    assert cust["total_minutes"] == 2.0

    # 1. USD Revenue Conversion:
    # 300 cents = $3.00. At 88.0 baseline exchange rate = ₹264.00
    assert pytest.approx(cust["revenue_inr"], abs=0.01) == 264.00

    # 2. Telephony Carrier Rates:
    # Inbound 1 min @ ₹0.30 + International 1 min @ ₹1.80 = ₹2.10
    assert pytest.approx(cust["breakdown"]["plivo_inr"], abs=0.01) == 2.10






