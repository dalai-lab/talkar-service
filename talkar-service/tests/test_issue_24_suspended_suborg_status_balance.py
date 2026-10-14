import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.customers import get_customer_status


@pytest.mark.asyncio
async def test_suspended_suborg_status_returns_master_wallet_balance():
    """
    Issue 24:
    When a sub-org is suspended, GET /customers/status must resolve the master
    wallet balance instead of returning a hardcoded 0 paise balance.
    """
    sub_org = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance", "suspension_message": "Balance below threshold"},
    )
    master_sub = Subscription(id=1, customer_id=1, plan="pro")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=250000)

    mock_db = MagicMock()

    # Route queries based on query content
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalars.return_value.first.return_value = sub_org
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        if "wallets.customer_id =" in q_str:
            # Query must target master_id (1), not sub_org.id (2)
            cid = query.whereclause.right.value
            if cid == 1:
                mock_res.scalar_one_or_none.return_value = master_wallet
            else:
                mock_res.scalar_one_or_none.return_value = None
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.dograh_client.get_org_config", AsyncMock(return_value=None)):
        resp = await get_customer_status(dograh_org_id=200, db=mock_db)

    assert resp["status"] == "suspended"
    assert resp["is_sub_org"] is True
    assert resp["billing_org_id"] == 1
    assert resp["plan"] == "pro"
    # Balance must reflect the master wallet's 250000 paise (₹2,500.00), NOT 0
    assert resp["balance_paise"] == 250000
    assert resp["suspension_reason"] == "zero_balance"
    assert resp["suspension_message"] == "Balance below threshold"


@pytest.mark.asyncio
async def test_suspended_master_status_returns_wallet_balance():
    """
    Issue 24:
    Master customer in suspended status continues to return their wallet balance.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "billing_issue"},
    )
    master_sub = Subscription(id=1, customer_id=1, plan="starter")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=15000)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalars.return_value.first.return_value = master
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.dograh_client.get_org_config", AsyncMock(return_value=None)):
        resp = await get_customer_status(dograh_org_id=100, db=mock_db)

    assert resp["status"] == "suspended"
    assert resp["is_sub_org"] is False
    assert resp["balance_paise"] == 15000
    assert resp["suspension_reason"] == "billing_issue"


@pytest.mark.asyncio
async def test_suspended_suborg_fallback_to_get_billing_wallet():
    """
    Issue 24:
    If direct wallet query returns None, fallback to get_billing_wallet accurately
    fetches the shared billing wallet.
    """
    sub_org = Customer(
        id=5,
        dograh_org_id=500,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"},
    )
    master_sub = Subscription(id=1, customer_id=1, plan="growth")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=80000)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalars.return_value.first.return_value = sub_org
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        if "wallets.customer_id =" in q_str:
            # Simulate wallet not found on direct query
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with (
        patch("services.dograh_client.get_org_config", AsyncMock(return_value=None)),
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))) as mock_gbw,
    ):
        resp = await get_customer_status(dograh_org_id=500, db=mock_db)

    assert resp["balance_paise"] == 80000
    mock_gbw.assert_awaited_once_with(mock_db, sub_org.id, allow_suspended=True)
