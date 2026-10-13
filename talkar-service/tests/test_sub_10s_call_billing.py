import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CALL_BLOCK_THRESHOLD_PAISE
from db.models import Customer, Subscription, Wallet, CallLog, WalletTransaction
from routers.billing import DograhDeductRequest, deduct_for_run
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_sub_10s_call_is_billed_one_minute():
    """A short call (e.g. 5s or 9s) must not be free; it must be billed 1 minute according to SOT line 281."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

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
    res_deduct.scalar_one_or_none.return_value = Wallet(id=1, customer_id=10, balance_paise=97500)
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = Wallet(id=1, customer_id=10, balance_paise=97500)

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub, res_deduct, res_refresh])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=801,
        duration_seconds=7,  # Sub-10s call!
        organization_id=100,
        mode="telephony",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")

        assert result["status"] == "ok"
        # Billed for 1 full minute (2500 paise)
        assert result["cost_paise"] == 2500
        assert result["new_balance_paise"] == 97500

        # Verify CallLog has cost_to_customer_paise == 2500
        call_logs = [obj for obj in added_objects if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        assert call_logs[0].cost_to_customer_paise == 2500
        assert call_logs[0].duration_seconds == 7

        # Verify WalletTransaction was recorded
        txns = [obj for obj in added_objects if isinstance(obj, WalletTransaction)]
        assert len(txns) == 1
        assert txns[0].amount_paise == -2500
        assert txns[0].type == "call_deduction"


@pytest.mark.asyncio
async def test_sub_10s_call_depleting_wallet_triggers_pause_and_block():
    """When sub-10s calls drain the wallet below CALL_BLOCK_THRESHOLD_PAISE, calls are blocked."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    # Balance before deduction is 51,000 paise (above 50,000 threshold)
    wallet = Wallet(id=1, customer_id=10, balance_paise=51000)
    # After deduction of 2,500 paise, balance drops to 48,500 paise (below 50,000 threshold)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=48500)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub, res_deduct, res_refresh])
    mock_db.add = MagicMock()

    req = DograhDeductRequest(
        workflow_run_id=802,
        duration_seconds=9,
        organization_id=100,
        mode="telephony",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
        patch("services.notification_service.notify_customer_service_paused", AsyncMock()) as mock_notify_paused,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync_block,
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")

        assert result["status"] == "ok"
        assert result["cost_paise"] == 2500
        mock_notify_paused.assert_awaited_once_with(10)
        mock_sync_block.assert_awaited_once_with(mock_db, 10)


@pytest.mark.asyncio
async def test_zero_duration_call_remains_free():
    """Unanswered or abnormal termination calls with duration <= 0 remain free (SOT line 278)."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, status="active", billing_org_id=None)
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
        workflow_run_id=803,
        duration_seconds=0,
        organization_id=100,
        mode="telephony",
    )

    with patch("services.billing_service.get_billing_wallet") as mock_wallet:
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result == {"status": "zero_duration_logged"}
        assert len(added_objects) == 1
        assert added_objects[0].cost_to_customer_paise == 0
        mock_wallet.assert_not_called()


@pytest.mark.asyncio
async def test_cron_deduct_for_run_sub_10s_is_billed():
    """Cron reconciliation deduct_for_run charges 1-minute rate for sub-10s calls."""
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    customer = Customer(id=10, dograh_org_id=100, status="active")
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=2500)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=97500)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = wallet_post

    mock_session.execute = AsyncMock(side_effect=[res_cust, res_sub, res_log, res_deduct, res_refresh])
    mock_session.refresh = AsyncMock()
    added = []
    mock_session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    with (
        patch("services.dograh_client.get_run", AsyncMock(return_value={
            "id": 804, "organization_id": 100, "status": "completed",
            "duration_seconds": 6, "mode": "telephony"
        })),
        patch("db.session.AsyncSessionLocal", return_value=mock_session),
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        await billing_service.deduct_for_run(804)

        call_logs = [obj for obj in added if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        assert call_logs[0].cost_to_customer_paise == 2500
        txns = [obj for obj in added if isinstance(obj, WalletTransaction)]
        assert len(txns) == 1
        assert txns[0].amount_paise == -2500
