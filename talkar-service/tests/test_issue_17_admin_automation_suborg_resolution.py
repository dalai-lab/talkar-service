import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription, TalkarAdmin
from routers.wallet import get_wallet
from routers.admin import get_customer_subscription
from routers.automation import wallet_alerts, list_customers


@pytest.mark.asyncio
async def test_get_wallet_resolves_master_wallet_for_sub_org():
    """
    Issue 17: GET /wallet/{customer_id} must resolve the master wallet
    for sub-orgs instead of returning 404 Wallet not found.
    """
    sub_org = Customer(id=20, billing_org_id=1, status="active", company_name="Client A")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=2500000, auto_recharge_enabled=True)
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()

    # Query 1: select(Wallet).where(Wallet.customer_id == 20) -> None
    wallet_res = MagicMock()
    wallet_res.scalar_one_or_none.return_value = None

    # Query 2 (in get_billing_wallet): select(Customer).where(Customer.id == 20) -> sub_org
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 3 (in get_billing_wallet): select(Customer).where(Customer.id == 1) -> master
    master = Customer(id=1, billing_org_id=None, status="active", company_name="Agency Master")
    master_cust_res = MagicMock()
    master_cust_res.scalar_one_or_none.return_value = master

    # Query 4 (in get_billing_wallet): select(Wallet).where(Wallet.customer_id == 1) -> master_wallet
    master_wallet_res = MagicMock()
    master_wallet_res.scalar_one_or_none.return_value = master_wallet

    db.execute.side_effect = [wallet_res, cust_res, master_cust_res, master_wallet_res]

    res = await get_wallet(customer_id=20, db=db, current_admin=admin)

    assert res["balance_paise"] == 2500000
    assert res["auto_recharge_enabled"] is True
    assert res["customer_id"] == 20
    assert res["master_customer_id"] == 1
    assert res["is_sub_org"] is True


@pytest.mark.asyncio
async def test_get_wallet_returns_direct_wallet_for_master():
    """
    Issue 17: GET /wallet/{customer_id} returns direct wallet for master customer.
    """
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000, auto_recharge_enabled=False)
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()
    wallet_res = MagicMock()
    wallet_res.scalar_one_or_none.return_value = master_wallet
    db.execute.return_value = wallet_res

    res = await get_wallet(customer_id=1, db=db, current_admin=admin)

    assert res["balance_paise"] == 500000
    assert res["auto_recharge_enabled"] is False
    assert res["customer_id"] == 1
    assert res["master_customer_id"] == 1
    assert res["is_sub_org"] is False


@pytest.mark.asyncio
async def test_get_wallet_returns_404_when_customer_and_wallet_not_found():
    """
    Issue 17: GET /wallet/{customer_id} returns 404 when wallet does not exist.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    db = AsyncMock()

    # Direct wallet query -> None
    wallet_res = MagicMock()
    wallet_res.scalar_one_or_none.return_value = None

    # Customer query -> None
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [wallet_res, cust_res]

    with pytest.raises(HTTPException) as exc_info:
        await get_wallet(customer_id=999, db=db, current_admin=admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Wallet not found"


@pytest.mark.asyncio
async def test_get_customer_subscription_inherits_master_for_sub_org():
    """
    Issue 17: GET /customers/{customer_id}/subscription must inherit master subscription
    when sub-org has no dedicated record instead of throwing 404.
    """
    sub_org = Customer(id=25, billing_org_id=2, status="active", company_name="SubOrg Brand")
    master_sub = Subscription(
        id=10,
        customer_id=2,
        plan="pro",
        per_minute_rate_paise=1500,
        custom_config={"concurrent_call_limit": 10},
        custom_plan_label="Pro Tier"
    )
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()

    # Query 1: select(Customer).where(Customer.id == 25)
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select(Subscription).where(Subscription.customer_id == 25) -> None
    sub_org_sub_res = MagicMock()
    sub_org_sub_res.scalar_one_or_none.return_value = None

    # Query 3: select(Subscription).where(Subscription.customer_id == 2) -> master_sub
    master_sub_res = MagicMock()
    master_sub_res.scalar_one_or_none.return_value = master_sub

    db.execute.side_effect = [cust_res, sub_org_sub_res, master_sub_res]

    res = await get_customer_subscription(customer_id=25, db=db, current_admin=admin)

    assert res["id"] == 10
    assert res["plan"] == "pro"
    assert res["per_minute_rate_paise"] == 1500
    assert res["custom_config"] == {"concurrent_call_limit": 10}
    assert res["is_inherited"] is True
    assert res["billing_org_id"] == 2


@pytest.mark.asyncio
async def test_get_customer_subscription_direct_subscription():
    """
    Issue 17: GET /customers/{customer_id}/subscription returns direct subscription for master.
    """
    master = Customer(id=2, billing_org_id=None, status="active", company_name="Master Agency")
    direct_sub = Subscription(
        id=12,
        customer_id=2,
        plan="enterprise",
        per_minute_rate_paise=2000
    )
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = master

    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = direct_sub

    db.execute.side_effect = [cust_res, sub_res]

    res = await get_customer_subscription(customer_id=2, db=db, current_admin=admin)

    assert res["id"] == 12
    assert res["plan"] == "enterprise"
    assert res["is_inherited"] is False
    assert res["billing_org_id"] is None


@pytest.mark.asyncio
async def test_get_customer_subscription_404_when_customer_not_found():
    """
    Issue 17: GET /customers/{customer_id}/subscription returns 404 when customer not found.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    db = AsyncMock()

    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = None
    db.execute.return_value = cust_res

    with pytest.raises(HTTPException) as exc_info:
        await get_customer_subscription(customer_id=999, db=db, current_admin=admin)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Customer not found"


@pytest.mark.asyncio
async def test_wallet_alerts_includes_active_sub_orgs():
    """
    Issue 17: GET /automation/v1/wallet/alerts joins on COALESCE(billing_org_id, id),
    ensuring active sub-orgs under low-balance master wallets are included.
    """
    db = AsyncMock()

    # Simulate query result returning both master and sub-org rows
    mock_row_master = MagicMock()
    mock_row_master.dograh_org_id = 101
    mock_row_master.balance_paise = 40000  # ₹400
    mock_row_master.threshold = 150000     # ₹1,500
    mock_row_master.customer_id = 1
    mock_row_master.is_sub_org = False

    mock_row_sub = MagicMock()
    mock_row_sub.dograh_org_id = 102
    mock_row_sub.balance_paise = 40000     # Shared master float: ₹400
    mock_row_sub.threshold = 150000
    mock_row_sub.customer_id = 20
    mock_row_sub.is_sub_org = True

    mock_res = [mock_row_master, mock_row_sub]
    db.execute.return_value = mock_res

    api_key = MagicMock()
    response = await wallet_alerts(db=db, api_key=api_key)

    alerts = response["low_balance_customers"]
    assert len(alerts) == 2

    # Master org alert
    assert alerts[0]["dograh_org_id"] == 101
    assert alerts[0]["balance_rupees"] == 400.0
    assert alerts[0]["is_sub_org"] is False

    # Sub-org alert (previously omitted!)
    assert alerts[1]["dograh_org_id"] == 102
    assert alerts[1]["balance_rupees"] == 400.0
    assert alerts[1]["is_sub_org"] is True


@pytest.mark.asyncio
async def test_list_customers_resolves_sub_org_wallet_and_subscription():
    """
    Issue 17: GET /automation/v1/customers populates effective wallet balance
    and tier for sub-orgs instead of defaulting to ₹0 and 'starter'.
    """
    db = AsyncMock()

    master = Customer(id=1, dograh_org_id=101, billing_org_id=None, company_name="Master Agency", status="active")
    sub_org = Customer(id=20, dograh_org_id=102, billing_org_id=1, company_name="SubOrg Client", status="active")

    # 1. select Customer
    cust_res = MagicMock()
    cust_res.scalars.return_value.all.return_value = [master, sub_org]

    # 2. select Wallet where customer_id.in_([1])
    wallet = Wallet(id=1, customer_id=1, balance_paise=850000)
    wallet_res = MagicMock()
    wallet_res.scalars.return_value.all.return_value = [wallet]

    # 3. select Subscription where customer_id.in_([1, 20])
    sub = Subscription(id=5, customer_id=1, plan="pro", per_minute_rate_paise=1500)
    sub_res = MagicMock()
    sub_res.scalars.return_value.all.return_value = [sub]

    db.execute.side_effect = [cust_res, wallet_res, sub_res]

    api_key = MagicMock()
    response = await list_customers(db=db, api_key=api_key)

    custs = response["customers"]
    assert len(custs) == 2

    # Master
    assert custs[0]["dograh_org_id"] == 101
    assert custs[0]["plan"] == "pro"
    assert custs[0]["balance_rupees"] == 8500.0

    # Sub-org resolves master wallet and pro plan
    assert custs[1]["dograh_org_id"] == 102
    assert custs[1]["plan"] == "pro"
    assert custs[1]["balance_rupees"] == 8500.0
