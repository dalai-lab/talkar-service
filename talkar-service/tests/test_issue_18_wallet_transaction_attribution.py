import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction, Subscription, TalkarAdmin
from services.billing_service import credit_wallet
from routers.billing import deduct_for_run, DograhDeductRequest, get_transactions_by_org
from routers.admin import get_customer_transactions


@pytest.mark.asyncio
async def test_deduct_for_run_stamps_sub_org_attribution():
    """
    Issue 18: deduct_for_run must stamp sub_org_id and dograh_org_id on WalletTransaction
    when a call deduction occurs for a sub-org workspace.
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="active"
    )
    master = Customer(
        id=1,
        billing_org_id=None,
        dograh_org_id=101,
        status="active"
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)

    db = AsyncMock()

    # Query 1: select(Customer).where(Customer.dograh_org_id == 102) -> sub_org
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select(CallLog) idempotency check -> None
    cl_check_res = MagicMock()
    cl_check_res.scalar_one_or_none.return_value = None

    # Query 3: select(Subscription) -> sub
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = sub

    # Query 4a: in get_billing_wallet -> customer
    bw_cust_res = MagicMock()
    bw_cust_res.scalar_one_or_none.return_value = sub_org

    # Query 4b: in get_billing_wallet -> master customer check
    bw_master_res = MagicMock()
    bw_master_res.scalar_one_or_none.return_value = master

    # Query 4c: in get_billing_wallet -> wallet
    bw_wallet_res = MagicMock()
    bw_wallet_res.scalar_one_or_none.return_value = master_wallet

    # Query 5: update(Wallet) returning wallet
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=499400)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 6: re-fetch wallet after auto-recharge
    refreshed_wallet_res = MagicMock()
    refreshed_wallet_res.scalar_one_or_none.return_value = updated_wallet

    db.execute.side_effect = [
        cust_res,
        cl_check_res,
        sub_res,
        bw_cust_res,
        bw_master_res,
        bw_wallet_res,
        update_res,
        refreshed_wallet_res,
    ]

    deduct_req = DograhDeductRequest(
        organization_id=102,
        duration_seconds=60,
        workflow_run_id=98765
    )

    with patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.dograh_client.get_run", AsyncMock(return_value=None)):
        res = await deduct_for_run(deduct_req, db=db)

    assert res["status"] == "ok"
    assert res["cost_paise"] == 600

    # Verify WalletTransaction was added with sub_org_id and dograh_org_id
    added_objects = [call[0][0] for call in db.add.call_args_list]
    wallet_txns = [o for o in added_objects if isinstance(o, WalletTransaction)]
    assert len(wallet_txns) == 1

    txn = wallet_txns[0]
    assert txn.customer_id == 1         # Master wallet
    assert txn.sub_org_id == 20         # Sub-org attribution
    assert txn.dograh_org_id == 102     # Sub-org Dograh org ID
    assert txn.dograh_run_id == 98765
    assert txn.amount_paise == -600


@pytest.mark.asyncio
async def test_deduct_for_run_master_customer_attribution():
    """
    Issue 18: deduct_for_run for a master organization sets customer_id and dograh_org_id,
    with sub_org_id set to None.
    """
    master = Customer(
        id=1,
        billing_org_id=None,
        dograh_org_id=101,
        status="active"
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)

    db = AsyncMock()

    # Query 1: select(Customer).where(Customer.dograh_org_id == 101) -> master
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = master

    # Query 2: select(CallLog) idempotency check -> None
    cl_check_res = MagicMock()
    cl_check_res.scalar_one_or_none.return_value = None

    # Query 3: select(Subscription) -> sub
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = sub

    # Query 4a: in get_billing_wallet -> master customer
    bw_cust_res = MagicMock()
    bw_cust_res.scalar_one_or_none.return_value = master

    # Query 4b: in get_billing_wallet -> wallet
    bw_wallet_res = MagicMock()
    bw_wallet_res.scalar_one_or_none.return_value = master_wallet

    # Query 5: update(Wallet) returning wallet
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=499400)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 6: re-fetch wallet after auto-recharge
    refreshed_wallet_res = MagicMock()
    refreshed_wallet_res.scalar_one_or_none.return_value = updated_wallet

    db.execute.side_effect = [
        cust_res,
        cl_check_res,
        sub_res,
        bw_cust_res,
        bw_wallet_res,
        update_res,
        refreshed_wallet_res,
    ]

    deduct_req = DograhDeductRequest(
        organization_id=101,
        duration_seconds=60,
        workflow_run_id=98766
    )

    with patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.dograh_client.get_run", AsyncMock(return_value=None)):
        res = await deduct_for_run(deduct_req, db=db)

    assert res["status"] == "ok"
    added_objects = [call[0][0] for call in db.add.call_args_list]
    wallet_txns = [o for o in added_objects if isinstance(o, WalletTransaction)]
    assert len(wallet_txns) == 1

    txn = wallet_txns[0]
    assert txn.customer_id == 1
    assert txn.sub_org_id is None
    assert txn.dograh_org_id == 101


@pytest.mark.asyncio
async def test_credit_wallet_attributes_sub_org_topup():
    """
    Issue 18: credit_wallet stamps sub_org_id and dograh_org_id when a sub-org customer is credited.
    """
    sub_org = Customer(id=30, billing_org_id=5, dograh_org_id=205, status="active")
    master_wallet = Wallet(id=1, customer_id=5, balance_paise=100000)

    db = AsyncMock()

    # Query 1: get_billing_wallet queries Customer(30)
    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org

    # Query 2: get_billing_wallet queries Customer(5) (master check)
    master = Customer(id=5, billing_org_id=None, status="active")
    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master

    # Query 3: get_billing_wallet queries Wallet(5)
    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    # Query 4: idempotency check (existing_txn) -> None
    idemp_res = MagicMock()
    idemp_res.scalar_one_or_none.return_value = None

    # Query 5: update(Wallet) -> returning wallet
    updated_wallet = Wallet(id=1, customer_id=5, balance_paise=600000)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 6: dograh_org_id lookup
    dograh_id_res = MagicMock()
    dograh_id_res.scalar_one_or_none.return_value = 205

    db.execute.side_effect = [
        bw_c1,
        bw_c2,
        bw_w,
        idemp_res,
        update_res,
        dograh_id_res,
    ]

    wallet = await credit_wallet(
        db,
        customer_id=30,
        amount_paise=500000,
        razorpay_order_id="order_suborg_topup_1",
        description="Sub-org branch deposit"
    )

    added_objects = [call[0][0] for call in db.add.call_args_list]
    txns = [o for o in added_objects if isinstance(o, WalletTransaction)]
    assert len(txns) == 1

    txn = txns[0]
    assert txn.customer_id == 5         # Master wallet
    assert txn.sub_org_id == 30         # Sub-org attribution
    assert txn.dograh_org_id == 205     # Dograh org ID attribution
    assert txn.amount_paise == 500000


@pytest.mark.asyncio
async def test_get_transactions_by_org_sub_org_includes_non_call_transactions():
    """
    Issue 18: Sub-org ledger in GET /transactions/by-org/{org_id} includes non-call
    transactions (top-ups, adjustments) attributed to this sub_org_id.
    """
    sub_org = Customer(id=20, billing_org_id=1, dograh_org_id=102, status="active")

    db = AsyncMock()

    # Query 1: select(Customer).where(Customer.dograh_org_id == 102) -> sub_org
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select(CallLog.dograh_run_id) -> []
    runs_res = MagicMock()
    runs_res.scalars.return_value.all.return_value = []

    # Query 3: count
    count_res = MagicMock()
    count_res.scalar.return_value = 2

    # Query 4: page transactions
    tx1 = WalletTransaction(
        id=1,
        customer_id=1,
        sub_org_id=20,
        dograh_org_id=102,
        type="top_up",
        amount_paise=200000,
        description="Branch top-up"
    )
    tx2 = WalletTransaction(
        id=2,
        customer_id=1,
        sub_org_id=20,
        dograh_org_id=102,
        type="manual_credit",
        amount_paise=50000,
        description="Promotional credit"
    )
    page_res = MagicMock()
    page_res.scalars.return_value.all.return_value = [tx1, tx2]

    db.execute.side_effect = [cust_res, runs_res, count_res, page_res]

    res = await get_transactions_by_org(org_id=102, db=db)

    txns = res["transactions"]
    assert len(txns) == 2
    assert txns[0]["sub_org_id"] == 20
    assert txns[0]["type"] == "top_up"
    assert txns[1]["sub_org_id"] == 20
    assert txns[1]["type"] == "manual_credit"


@pytest.mark.asyncio
async def test_admin_get_customer_transactions_attributable_ledger():
    """
    Issue 18: Admin endpoint GET /customers/{customer_id}/transactions returns
    transactions attributed to the specific sub-org workspace.
    """
    sub_org = Customer(id=20, billing_org_id=1, dograh_org_id=102, status="active")
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()

    # Query 1: select Customer -> sub_org
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: count -> 1
    count_res = MagicMock()
    count_res.scalar.return_value = 1

    # Query 3: transactions
    tx = WalletTransaction(
        id=55,
        customer_id=1,
        sub_org_id=20,
        dograh_org_id=102,
        type="call_deduction",
        amount_paise=-1200,
        description="Call deduction for run 777",
        dograh_run_id=777
    )
    tx_res = MagicMock()
    tx_res.scalars.return_value.all.return_value = [tx]

    db.execute.side_effect = [cust_res, count_res, tx_res]

    res = await get_customer_transactions(customer_id=20, db=db, current_admin=admin)

    assert res["total"] == 1
    txns = res["transactions"]
    assert len(txns) == 1
    assert txns[0]["id"] == 55
    assert txns[0]["sub_org_id"] == 20
    assert txns[0]["dograh_org_id"] == 102
    assert txns[0]["amount_paise"] == -1200
