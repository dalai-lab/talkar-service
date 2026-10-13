import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CALL_BLOCK_THRESHOLD_PAISE
from db.models import Customer, Subscription, Wallet
from routers.billing import DograhDeductRequest, deduct_for_run


@pytest.mark.asyncio
async def test_deduct_for_run_refreshes_wallet_and_skips_pause_warning_on_successful_recharge():
    """When a call deduction drops balance below threshold, but auto-recharge replenishes it,
    deduct_for_run must re-query the authoritative DB balance and NOT send a false service paused notification."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, billing_org_id=None)
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

    # 4. Atomic update of wallet deduction (balance dropped to 40,000 paise <= 50,000 threshold)
    wallet_post_deduct = Wallet(id=1, customer_id=10, balance_paise=40000)
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_post_deduct

    # 5. Re-queried wallet after check_and_trigger_auto_recharge has credited 500,000 paise (balance 540,000)
    wallet_post_recharge = Wallet(id=1, customer_id=10, balance_paise=540000)
    res_recharge = MagicMock()
    res_recharge.scalar_one_or_none.return_value = wallet_post_recharge

    mock_db.execute = AsyncMock(
        side_effect=[res_cust, res_log, res_sub, res_deduct, res_recharge]
    )

    req = DograhDeductRequest(
        workflow_run_id=501,
        duration_seconds=60,
        organization_id=100,
    )

    with (
        patch(
            "services.billing_service.get_billing_wallet",
            AsyncMock(return_value=(wallet_post_deduct, 10)),
        ),
        patch("services.billing_service.check_and_trigger_auto_recharge") as mock_recharge,
        patch("services.notification_service.notify_customer_service_paused") as mock_notify_paused,
        patch("services.billing_service.sync_wallet_block_policy") as mock_sync_block,
    ):
        result = await deduct_for_run(req, db=mock_db)

        # check_and_trigger_auto_recharge was called
        mock_recharge.assert_awaited_once_with(mock_db, 10)

        # Because wallet was re-queried and balance is 540,000 > threshold, pause warning must NOT fire!
        mock_notify_paused.assert_not_called()
        mock_sync_block.assert_not_called()

        assert result["status"] == "ok"
        assert result["new_balance_paise"] == 540000


@pytest.mark.asyncio
async def test_deduct_for_run_alerts_paused_when_balance_remains_below_threshold():
    """When auto-recharge is not triggered or fails, and balance remains <= threshold,
    deduct_for_run correctly fires the service paused alert."""
    mock_db = AsyncMock()

    customer = Customer(id=10, dograh_org_id=100, billing_org_id=None)
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

    # 4. Wallet post deduct is 40,000 paise (<= 50,000 threshold)
    wallet_low = Wallet(id=1, customer_id=10, balance_paise=40000)
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = wallet_low

    # 5. Wallet re-queried is still 40,000 (auto-recharge did not recharge)
    res_rechecked = MagicMock()
    res_rechecked.scalar_one_or_none.return_value = wallet_low

    mock_db.execute = AsyncMock(
        side_effect=[res_cust, res_log, res_sub, res_deduct, res_rechecked]
    )

    req = DograhDeductRequest(
        workflow_run_id=502,
        duration_seconds=60,
        organization_id=100,
    )

    with (
        patch(
            "services.billing_service.get_billing_wallet",
            AsyncMock(return_value=(wallet_low, 10)),
        ),
        patch("services.billing_service.check_and_trigger_auto_recharge") as mock_recharge,
        patch("services.notification_service.notify_customer_service_paused") as mock_notify_paused,
        patch("services.billing_service.sync_wallet_block_policy") as mock_sync_block,
    ):
        result = await deduct_for_run(req, db=mock_db)

        mock_recharge.assert_awaited_once_with(mock_db, 10)
        # Low balance persisted -> paused alert fired
        mock_notify_paused.assert_awaited_once_with(10)
        mock_sync_block.assert_awaited_once_with(mock_db, 10)

        assert result["status"] == "ok"
        assert result["new_balance_paise"] == 40000
