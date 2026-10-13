import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog
from routers.billing import DograhDeductRequest, deduct_for_run
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_agent_building_webrtc_deduct_is_free():
    """WebRTC calls during agent_building must be logged with 0 cost and not deducted from wallet."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="agent_building", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)

    # 1. Customer query
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    # 2. CallLog idempotency check (none existing)
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None

    # 3. Subscription query
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub])

    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=601,
        duration_seconds=120,
        organization_id=100,
        mode="webrtc",
    )

    with (
        patch("services.billing_service.get_billing_wallet") as mock_get_wallet,
        patch("services.billing_service.check_and_trigger_auto_recharge") as mock_auto_recharge,
        patch("services.billing_service.sync_wallet_block_policy") as mock_sync_block,
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")

        assert result == {"status": "agent_building_free_call_logged", "cost_paise": 0}
        # Verify CallLog added with ₹0 cost
        assert len(added_objects) == 1
        call_log = added_objects[0]
        assert isinstance(call_log, CallLog)
        assert call_log.cost_to_customer_paise == 0
        assert call_log.dograh_run_id == 601
        assert call_log.duration_seconds == 120

        # Verify no wallet operations or block sync occurred
        mock_get_wallet.assert_not_called()
        mock_auto_recharge.assert_not_called()
        mock_sync_block.assert_not_called()
        mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_building_smallwebrtc_deduct_is_free():
    """smallwebrtc calls during agent_building must also be logged with 0 cost."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="agent_building", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=602,
        duration_seconds=45,
        organization_id=100,
        mode="smallwebrtc",
    )

    result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
    assert result == {"status": "agent_building_free_call_logged", "cost_paise": 0}
    assert added_objects[0].cost_to_customer_paise == 0


@pytest.mark.asyncio
async def test_agent_building_omitted_mode_fetches_from_dograh():
    """If mode is omitted from request, fallback queries Dograh to detect webrtc mode."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="agent_building", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=603,
        duration_seconds=75,
        organization_id=100,
        mode=None,  # omitted
    )

    with patch("services.dograh_client.get_run", AsyncMock(return_value={"id": 603, "mode": "webrtc"})):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result == {"status": "agent_building_free_call_logged", "cost_paise": 0}
        assert added_objects[0].cost_to_customer_paise == 0


@pytest.mark.asyncio
async def test_agent_building_telephony_is_charged():
    """Telephony calls during agent_building are NOT free and are deducted normally."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="agent_building", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub, res_deduct, res_refresh])
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    req = DograhDeductRequest(
        workflow_run_id=604,
        duration_seconds=60,
        organization_id=100,
        mode="telephony",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result["status"] == "ok"
        assert result["cost_paise"] == 2500


@pytest.mark.asyncio
async def test_active_customer_webrtc_is_charged():
    """WebRTC calls for active customers are billed normally."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub, res_deduct, res_refresh])
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    req = DograhDeductRequest(
        workflow_run_id=605,
        duration_seconds=60,
        organization_id=100,
        mode="webrtc",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result["status"] == "ok"
        assert result["cost_paise"] == 2500


@pytest.mark.asyncio
async def test_cron_deduct_for_run_agent_building_webrtc_is_free():
    """Cron reconciliation deduct_for_run must log 0 cost for agent_building WebRTC run."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    customer = Customer(id=10, dograh_org_id=100, status="agent_building")
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    wallet = Wallet(id=1, customer_id=10, balance_paise=0)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = wallet

    mock_session.execute = AsyncMock(side_effect=[res_cust, res_sub, res_log, res_wallet])
    added = []
    mock_session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    with (
        patch("services.dograh_client.get_run", AsyncMock(return_value={
            "id": 701, "organization_id": 100, "status": "completed",
            "duration_seconds": 90, "mode": "webrtc"
        })),
        patch("db.session.AsyncSessionLocal", return_value=mock_session),
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()) as mock_auto_recharge,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block,
    ):
        await billing_service.deduct_for_run(701)

        assert len(added) == 1
        assert added[0].cost_to_customer_paise == 0
        mock_auto_recharge.assert_not_called()
        mock_sync_block.assert_not_called()


@pytest.mark.asyncio
async def test_check_quota_active_customer_webrtc_enforces_concurrency_limit():
    """Active customer WebRTC calls must NOT bypass tier concurrency limit."""
    from routers.billing import DograhQuotaRequest, check_quota

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    wallet = Wallet(id=1, customer_id=10, balance_paise=500000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="webrtc",
        active_calls=3,  # Starter tier limit is 2
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))):
        res = await check_quota(data=req, db=mock_db, _token="test_token")
        assert res["has_quota"] is False
        assert res["reason"] == "concurrency_limit_exceeded"
        assert res["active_calls"] == 3
        assert res["concurrent_call_limit"] == 2


@pytest.mark.asyncio
async def test_check_quota_active_customer_webrtc_enforces_wallet_reserve():
    """Active customer WebRTC calls must NOT bypass wallet reserve checks."""
    from routers.billing import DograhQuotaRequest, check_quota

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    # Rate 2500 paise/min, max call duration 900s (15 min) -> required reserve for 2 calls = 2 * 15 * 2500 = 75,000 paise
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    # Balance is 60,000 paise (above ₹500 CALL_BLOCK_THRESHOLD_PAISE, but below 75,000 required reserve for 2 calls)
    wallet = Wallet(id=1, customer_id=10, balance_paise=60000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="webrtc",
        active_calls=2,  # Starter tier limit is 2 (passes concurrency check)
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.trigger_auto_recharge_background", AsyncMock()),
    ):
        res = await check_quota(data=req, db=mock_db, _token="test_token")
        assert res["has_quota"] is False
        assert res["reason"] == "insufficient_reserve"
        assert res["required_paise"] == 75000


@pytest.mark.asyncio
async def test_check_quota_active_customer_textchat_bypasses_voice_reserve():
    """Active customer textchat is zero-cost non-voice interaction and bypasses voice reserve."""
    from routers.billing import DograhQuotaRequest, check_quota

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    # Balance is 60,000 paise (above ₹500 CALL_BLOCK_THRESHOLD_PAISE)
    wallet = Wallet(id=1, customer_id=10, balance_paise=60000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(side_effect=[res_cust])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="textchat",
        active_calls=1,
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))):
        res = await check_quota(data=req, db=mock_db, _token="test_token")
        assert res["has_quota"] is True


@pytest.mark.asyncio
async def test_check_quota_agent_building_caps_playground_concurrency():
    """Customer in agent_building is capped at 2 concurrent playground sessions to prevent abuse."""
    from routers.billing import DograhQuotaRequest, check_quota

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="agent_building", billing_org_id=None)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(side_effect=[res_cust])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="webrtc",
        active_calls=3,  # Exceeds cap of 2
    )

    res = await check_quota(data=req, db=mock_db, _token="test_token")
    assert res["has_quota"] is False
    assert res["reason"] == "concurrency_limit_exceeded"
    assert res["concurrent_call_limit"] == 2


@pytest.mark.asyncio
async def test_check_quota_suspended_customer_webrtc_rejected():
    """Suspended customer attempting WebRTC is rejected."""
    from routers.billing import DograhQuotaRequest, check_quota

    mock_db = MagicMock()
    customer = Customer(id=10, dograh_org_id=100, status="suspended", billing_org_id=None)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(side_effect=[res_cust])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="webrtc",
        active_calls=1,
    )

    res = await check_quota(data=req, db=mock_db, _token="test_token")
    assert res["has_quota"] is False
    assert res["reason"] == "customer_status_suspended"
