import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from jobs.cron import cleanup_abandoned_signups
from routers.billing import create_topup_order, TopupRequest


@pytest.mark.asyncio
async def test_cron_cleanup_skips_master_with_active_child_suborgs():
    """
    Test Blocker 13:
    When a master signup in pending_approval has active child sub-organizations,
    cleanup_abandoned_signups skips deletion of the master customer.
    """
    row = MagicMock()
    row.id = 501
    row.dograh_org_id = 5001

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    child_row = MagicMock()
    child_row.id = 502

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "interval '7 days'" in q_str:
            res.__iter__.return_value = []
        elif "interval '30 days'" in q_str:
            res.__iter__.return_value = [row]
        elif "SELECT 1 FROM customers" in q_str:
            # Active child sub-org found!
            res.__iter__.return_value = [child_row]
        else:
            res.__iter__.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.delete_org", AsyncMock()) as mock_delete_org:
        await cleanup_abandoned_signups(ctx={})

    # Master org must NOT be deleted because active children exist
    mock_delete_org.assert_not_called()
    executed_sqls = [str(call[0][0]) for call in mock_db.execute.call_args_list]
    assert not any("DELETE FROM customers WHERE id = :id" in s for s in executed_sqls)


@pytest.mark.asyncio
async def test_create_topup_order_rejects_fraud_or_tos_suspension():
    """
    Test Blocker 14:
    Account suspended with reason 'fraud' or 'tos_violation' cannot create a top-up order.
    Returns HTTP 403.
    """
    cust_fraud = Customer(
        id=1,
        dograh_org_id=10,
        status="suspended",
        onboarding_form={"suspension_reason": "fraud"}
    )
    mock_db = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = cust_fraud
    mock_db.execute = AsyncMock(return_value=res)

    with patch("routers.billing.verify_customer_access", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await create_topup_order(
                data=TopupRequest(dograh_org_id=10, amount_rupees=1000),
                db=mock_db,
                x_talkar_email="user@test.com"
            )
        assert exc_info.value.status_code == 403
        assert "Account suspended by administration" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_topup_order_allows_zero_balance_suspension():
    """
    Test Blocker 14:
    Account suspended with reason 'zero_balance' CAN create a top-up order.
    """
    cust_normal = Customer(
        id=2,
        dograh_org_id=20,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub = Subscription(id=2, customer_id=2, plan="starter")

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = cust_normal

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    with patch("routers.billing.verify_customer_access", AsyncMock()), \
         patch("services.razorpay_client.create_topup_order", AsyncMock(return_value={"id": "order_123"})):
        resp = await create_topup_order(
            data=TopupRequest(dograh_org_id=20, amount_rupees=1000),
            db=mock_db,
            x_talkar_email="user@test.com"
        )
        assert resp["razorpay_order_id"] == "order_123"
        assert resp["amount_paise"] == 100000
