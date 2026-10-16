import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, Agent, TalkarAdmin
from routers.admin import UpdateAgentRateRequest, update_agent_rate, update_agent_rate_direct
from routers.billing import DograhDeductRequest, DograhQuotaRequest, deduct_for_run, check_quota
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_admin_update_agent_rate_endpoints():
    """Both admin rate update endpoints should update agent.per_minute_rate_paise."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    agent = Agent(id=5, customer_id=10, dograh_workflow_id=101, per_minute_rate_paise=None)
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    mock_db.execute = AsyncMock(return_value=res_agent)

    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", password_hash="hash")
    req = UpdateAgentRateRequest(per_minute_rate_paise=1500)

    # 1. Test update_agent_rate (via customer_id)
    res1 = await update_agent_rate(customer_id=10, agent_id=5, data=req, db=mock_db, current_admin=admin)
    assert res1 == {"status": "ok", "per_minute_rate_paise": 1500}
    assert agent.per_minute_rate_paise == 1500

    # 2. Test update_agent_rate_direct (via agent_id only)
    req2 = UpdateAgentRateRequest(per_minute_rate_paise=1800)
    res2 = await update_agent_rate_direct(agent_id=5, data=req2, db=mock_db, current_admin=admin)
    assert res2 == {"status": "ok", "per_minute_rate_paise": 1800}
    assert agent.per_minute_rate_paise == 1800


@pytest.mark.asyncio
async def test_deduct_for_run_uses_custom_agent_rate_and_stamps_agent_id():
    """deduct_for_run must use agent.per_minute_rate_paise when set and stamp agent_id in CallLog."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    # Plan starter rate is 600 paise
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    # Agent custom rate is 1500 paise
    agent = Agent(id=42, customer_id=10, dograh_workflow_id=999, per_minute_rate_paise=1500)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=97000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_agent, res_sub, res_deduct, res_refresh])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=901,
        duration_seconds=120,  # 2 minutes
        organization_id=100,
        mode="telephony",
        workflow_id=999,
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")

        assert result["status"] == "ok"
        # 2 minutes * 1500 paise = 3000 paise (instead of starter 1200 paise)
        assert result["cost_paise"] == 3000

        call_logs = [obj for obj in added_objects if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        assert call_logs[0].cost_to_customer_paise == 3000
        # agent_id is correctly stamped to the Agent's primary key!
        assert call_logs[0].agent_id == 42


@pytest.mark.asyncio
async def test_deduct_for_run_falls_back_to_sub_rate_when_no_agent_rate():
    """If agent has no custom rate, deduct_for_run uses subscription rate but still stamps agent_id."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    # Agent custom rate is None
    agent = Agent(id=43, customer_id=10, dograh_workflow_id=998, per_minute_rate_paise=None)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=98800)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_agent, res_sub, res_deduct, res_refresh])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=902,
        duration_seconds=120,  # 2 minutes
        organization_id=100,
        mode="telephony",
        workflow_id=998,
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")

        assert result["status"] == "ok"
        # 2 minutes * 600 paise = 1200 paise
        assert result["cost_paise"] == 1200

        call_logs = [obj for obj in added_objects if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        assert call_logs[0].cost_to_customer_paise == 1200
        assert call_logs[0].agent_id == 43


@pytest.mark.asyncio
async def test_cron_deduct_for_run_uses_custom_agent_rate():
    """Cron reconciliation deduct_for_run uses agent.per_minute_rate_paise and stamps agent_id."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    customer = Customer(id=10, dograh_org_id=100, status="active")
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    agent = Agent(id=77, customer_id=10, dograh_workflow_id=555, per_minute_rate_paise=2000)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=98000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_session.execute = AsyncMock(side_effect=[res_cust, res_sub, res_log, res_agent, res_deduct, res_refresh])
    mock_session.refresh = AsyncMock()
    added = []
    mock_session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    with (
        patch("services.dograh_client.get_run", AsyncMock(return_value={
            "id": 903, "organization_id": 100, "status": "completed",
            "duration_seconds": 60, "mode": "telephony", "workflow_id": 555
        })),
        patch("db.session.AsyncSessionLocal", return_value=mock_session),
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        await billing_service.deduct_for_run(903)

        call_logs = [obj for obj in added if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        # 1 minute * 2000 paise = 2000 paise
        assert call_logs[0].cost_to_customer_paise == 2000
        assert call_logs[0].agent_id == 77


@pytest.mark.asyncio
async def test_check_quota_scales_reserve_with_custom_agent_rate():
    """check_quota calculates reserve requirement with custom agent rate when workflow_id is provided."""
    mock_db = MagicMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    # Base starter rate 600 paise; max_call_duration 900s (15 min) -> base max_call_cost = 15 * 600 = 9000 paise
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    # Agent custom rate 2000 paise -> max_call_cost = 15 * 2000 = 30000 paise
    agent = Agent(id=88, customer_id=10, dograh_workflow_id=777, per_minute_rate_paise=2000)

    # Customer wallet has 20,000 paise (enough for base 9000 paise, but NOT enough for custom agent 30000 paise)
    wallet = Wallet(id=1, customer_id=10, balance_paise=20000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub, res_agent])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="telephony",
        active_calls=1,
        workflow_id=777,
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await check_quota(data=req, db=mock_db, _token="test_token")
        # Should be rejected because 20,000 < 30,000 paise
        assert result["has_quota"] is False
        assert result["reason"] == "insufficient_balance"


@pytest.mark.asyncio
async def test_update_agent_rate_rejects_below_cogs_floor():
    """Setting an agent rate below provider COGS floor (e.g. 300 paise on ElevenLabs) must raise HTTPException(400)."""
    from fastapi import HTTPException

    mock_db = MagicMock()
    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", password_hash="hash")

    # Agent on org using ElevenLabs
    agent = Agent(id=15, customer_id=10, dograh_org_id=100, dograh_workflow_id=301, per_minute_rate_paise=None)
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    mock_db.execute = AsyncMock(return_value=res_agent)

    # ElevenLabs floor is 1200 paise. Admin attempts to set 300 paise (₹3.00/min).
    req = UpdateAgentRateRequest(per_minute_rate_paise=300)

    with patch("services.dograh_client.get_org_config", AsyncMock(return_value={
        "byok": {"pipeline": {"tts": {"provider": "elevenlabs"}}}
    })):
        with pytest.raises(HTTPException) as exc_info:
            await update_agent_rate(customer_id=10, agent_id=15, data=req, db=mock_db, current_admin=admin)
        assert exc_info.value.status_code == 400
        assert "below provider floor" in exc_info.value.detail

        with pytest.raises(HTTPException) as exc_info_direct:
            await update_agent_rate_direct(agent_id=15, data=req, db=mock_db, current_admin=admin)
        assert exc_info_direct.value.status_code == 400
        assert "below provider floor" in exc_info_direct.value.detail


@pytest.mark.asyncio
async def test_deduct_clamps_legacy_below_floor_agent_rate():
    """If an agent had a legacy rate below the COGS floor, deduct_for_run clamps it to the floor."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    # Plan pro uses elevenlabs (floor 1200 paise)
    sub = Subscription(id=1, customer_id=10, plan="pro", per_minute_rate_paise=1800)
    # Legacy agent rate is set to 300 paise (below 1200 floor)
    agent = Agent(id=49, customer_id=10, dograh_workflow_id=991, per_minute_rate_paise=300)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    # 2 mins * floor 1200 paise = 2400 paise deduction -> 97600
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=97600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_agent = MagicMock()
    res_agent.scalar_one_or_none.return_value = agent
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_agent, res_sub, res_deduct, res_refresh])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=909,
        duration_seconds=120,  # 2 minutes
        organization_id=100,
        mode="telephony",
        workflow_id=991,
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result["status"] == "ok"
        # 2 minutes * 1200 paise (clamped floor) = 2400 paise, NOT 2 * 300 = 600 paise
        assert result["cost_paise"] == 2400
        call_logs = [obj for obj in added_objects if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        assert call_logs[0].cost_to_customer_paise == 2400


@pytest.mark.asyncio
async def test_set_custom_pricing_rejects_below_cogs_floor():
    """Admin setting custom pricing with ElevenLabs voice and rate below 1200 paise is rejected with 400."""
    from routers.admin import set_custom_pricing, SetCustomPricingRequest
    from fastapi import HTTPException

    mock_db = AsyncMock()
    customer = Customer(id=10, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    req = SetCustomPricingRequest(
        per_minute_rate_paise=300,  # Below 1200 floor for elevenlabs
        concurrent_call_limit=2,
        max_call_duration_seconds=900,
        activation_deposit_paise=10000,
        tts_provider="elevenlabs",
    )
    admin = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await set_custom_pricing(customer_id=10, data=req, db=mock_db, current_admin=admin)

    assert exc_info.value.status_code == 400
    assert "below provider floor" in exc_info.value.detail


@pytest.mark.asyncio
async def test_fix_6_set_custom_pricing_allows_pending_deposit():
    """Verify that set_custom_pricing allows customers in pending_deposit state."""
    from routers.admin import set_custom_pricing, SetCustomPricingRequest

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    customer = Customer(id=10, status="pending_deposit", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_suborgs = MagicMock()
    res_suborgs.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub, res_suborgs])

    req = SetCustomPricingRequest(
        per_minute_rate_paise=1500,
        concurrent_call_limit=5,
        max_call_duration_seconds=900,
        activation_deposit_paise=50000,
        tts_provider="deepgram",
    )
    admin = MagicMock()

    with patch("services.provisioning_service.run_provisioning", AsyncMock()), \
         patch("services.dograh_client.upsert_org_config", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):

        res = await set_custom_pricing(customer_id=10, data=req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    assert sub.plan == "custom"
    assert sub.per_minute_rate_paise == 1500


@pytest.mark.asyncio
async def test_fix_6_retry_provisioning_allows_pending_deposit():
    """Verify that retry_provisioning allows customers in pending_deposit state."""
    from routers.admin import retry_provisioning

    mock_db = MagicMock()
    customer = Customer(id=15, status="pending_deposit")

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    admin = MagicMock()
    with patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov:
        res = await retry_provisioning(customer_id=15, db=mock_db, current_admin=admin)

    assert res["status"] == "provisioning_complete"
    mock_prov.assert_awaited_once_with(15)


