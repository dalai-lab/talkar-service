import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, Agent
from routers.billing import DograhQuotaRequest, check_quota
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_check_quota_below_threshold_dispatches_background_recharge_without_blocking():
    """check_quota dispatches auto-recharge in the background and returns immediately when balance is below threshold."""
    mock_db = MagicMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    wallet = Wallet(id=1, customer_id=10, balance_paise=20000)  # below 50,000 threshold

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    req = DograhQuotaRequest(
        organization_id=100,
        mode="telephony",
        active_calls=1,
    )

    created_tasks = []
    original_create_task = asyncio.create_task

    def tracked_create_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task

    mock_bg_recharge = AsyncMock()

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.trigger_auto_recharge_background", mock_bg_recharge),
        patch("asyncio.create_task", side_effect=tracked_create_task),
    ):
        result = await check_quota(data=req, db=mock_db, _token="test_token")

        assert result["has_quota"] is False
        assert result["reason"] == "insufficient_balance"
        assert result["balance_paise"] == 20000
        mock_bg_recharge.assert_called_once_with(10)
        assert len(created_tasks) == 1

    # Cleanup background task
    for t in created_tasks:
        t.cancel()


@pytest.mark.asyncio
async def test_check_quota_below_reserve_dispatches_background_recharge_without_blocking():
    """check_quota dispatches auto-recharge in the background when balance is below reserve for concurrent calls."""
    mock_db = MagicMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    # Base starter rate 600 paise; max_call_duration 900s (15 min) -> max_call_cost = 9000 paise.
    # 2 concurrent calls limit in starter tier.
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    # Wallet has 60,000 paise (> 50,000 threshold).
    # Custom agent rate of 2500 paise/min -> max_call_cost = 15 * 2500 = 37,500 paise.
    # 2 concurrent calls -> total_required_reserve = 75,000 paise (> 60,000 wallet balance).
    wallet = Wallet(id=1, customer_id=10, balance_paise=60000)
    agent = Agent(id=55, customer_id=10, dograh_workflow_id=999, per_minute_rate_paise=2500)

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
        active_calls=2,
        workflow_id=999,
    )

    created_tasks = []
    original_create_task = asyncio.create_task

    def tracked_create_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task

    mock_bg_recharge = AsyncMock()

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.trigger_auto_recharge_background", mock_bg_recharge),
        patch("asyncio.create_task", side_effect=tracked_create_task),
    ):
        result = await check_quota(data=req, db=mock_db, _token="test_token")

        assert result["has_quota"] is False
        assert result["reason"] == "insufficient_reserve"
        assert result["balance_paise"] == 60000
        assert result["required_paise"] == 75000
        mock_bg_recharge.assert_called_once_with(10)
        assert len(created_tasks) == 1

    # Cleanup background task
    for t in created_tasks:
        t.cancel()


@pytest.mark.asyncio
async def test_trigger_auto_recharge_background_creates_independent_session():
    """trigger_auto_recharge_background opens an independent AsyncSessionLocal session and executes auto-recharge."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("db.session.AsyncSessionLocal", return_value=mock_session_ctx) as mock_session_maker,
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()) as mock_recharge,
    ):
        await billing_service.trigger_auto_recharge_background(customer_id=10)

        mock_session_maker.assert_called_once()
        mock_recharge.assert_awaited_once_with(mock_session, 10)


@pytest.mark.asyncio
async def test_trigger_auto_recharge_background_handles_exceptions_gracefully():
    """trigger_auto_recharge_background catches exceptions from check_and_trigger_auto_recharge and logs them."""
    mock_session = AsyncMock()
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("db.session.AsyncSessionLocal", return_value=mock_session_ctx),
        patch(
            "services.billing_service.check_and_trigger_auto_recharge",
            AsyncMock(side_effect=RuntimeError("Razorpay connection timeout")),
        ),
    ):
        # Should not raise exception
        await billing_service.trigger_auto_recharge_background(customer_id=10)


@pytest.mark.asyncio
async def test_check_quota_sufficient_balance_grants_quota_without_auto_recharge():
    """check_quota grants quota when balance is sufficient and does not trigger auto-recharge."""
    mock_db = MagicMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    # Required for 1 call is 9000 paise; wallet has 100,000 paise
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    req = DograhQuotaRequest(
        organization_id=100,
        mode="telephony",
        active_calls=1,
    )

    mock_bg_recharge = AsyncMock()

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.trigger_auto_recharge_background", mock_bg_recharge),
    ):
        result = await check_quota(data=req, db=mock_db, _token="test_token")

        assert result["has_quota"] is True
        mock_bg_recharge.assert_not_called()
