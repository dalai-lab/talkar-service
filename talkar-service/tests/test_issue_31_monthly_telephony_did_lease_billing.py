import sys
from pathlib import Path
import datetime
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, WalletTransaction, PhoneNumber, TransactionType, TalkarAdmin, Invoice
from services.billing_service import (
    charge_initial_phone_number_lease,
    bill_monthly_phone_number_leases,
)
from routers.admin import (
    assign_phone_number,
    AssignPhoneNumberRequest,
    release_customer_phone_number,
)


@pytest.mark.asyncio
async def test_free_phone_number_allowance_bypasses_initial_charge():
    """Verify Issue 31: First phone number within tier allowance incurs zero initial charge."""
    customer = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub = Subscription(id=1, customer_id=1, plan="starter")  # free_phone_numbers: 1
    wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    pn = PhoneNumber(id=1, customer_id=1, number="+919876543210", status="active")

    mock_db = MagicMock()
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
        elif "count(phone_numbers.id)" in q_str:
            mock_res.scalar_one.return_value = 0  # 0 existing active numbers
        elif "customers.id IN" in q_str or "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [1]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))):
        txn = await charge_initial_phone_number_lease(mock_db, customer_id=1, phone_number=pn)

    assert txn is None
    assert pn.last_billed_at is not None
    assert wallet.balance_paise == 1000000, "Wallet balance must remain unchanged for free number"
    assert len([obj for obj in added_objects if isinstance(obj, WalletTransaction)]) == 0


@pytest.mark.asyncio
async def test_extra_phone_number_triggers_initial_lease_deduction():
    """Verify Issue 31: Additional phone number beyond plan allowance deducts initial monthly lease."""
    customer = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, company_name="Corp A")
    sub = Subscription(id=1, customer_id=1, plan="starter")  # free_phone_numbers: 1
    wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    new_pn = PhoneNumber(id=2, customer_id=1, number="+919876543211", status="active")

    mock_db = MagicMock()
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_db.flush = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
        elif "count(phone_numbers.id)" in q_str:
            mock_res.scalar_one.return_value = 1  # 1 existing active number, so 2nd number is billable!
        elif "customers.id IN" in q_str or "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [1]
        elif "RETURNING" in q_str:
            wallet.balance_paise = 950000  # 1000000 - 50000
            mock_res.scalar_one_or_none.return_value = wallet
        elif "customers.dograh_org_id" in q_str and "customers.id =" in q_str:
            mock_row = MagicMock()
            mock_row.dograh_org_id = 10
            mock_row.company_name = "Corp A"
            mock_res.fetchone.return_value = mock_row
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_phone_number_lease_billed", AsyncMock()) as mock_notif:

        txn = await charge_initial_phone_number_lease(mock_db, customer_id=1, phone_number=new_pn)

    assert txn is not None
    assert txn.type == TransactionType.PHONE_NUMBER_LEASE.value
    assert txn.amount_paise == -50000  # ₹500
    assert txn.customer_id == 1
    assert txn.sub_org_id is None
    assert wallet.balance_paise == 950000
    assert new_pn.last_billed_at is not None
    mock_notif.assert_awaited_once()


@pytest.mark.asyncio
async def test_sub_org_extra_phone_number_deducts_from_master_wallet_with_attribution():
    """Verify Issue 31: Sub-org extra phone number deducts from master wallet and attributes sub_org_id."""
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, company_name="Agency Master")
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, company_name="Sub Client A")
    master_sub = Subscription(id=1, customer_id=1, plan="starter")  # free_phone_numbers: 1
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=800000)

    sub_pn = PhoneNumber(id=5, customer_id=2, number="+919876543999", status="active")

    mock_db = MagicMock()
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_db.flush = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "count(phone_numbers.id)" in q_str:
            mock_res.scalar_one.return_value = 1  # Master already used the 1 free slot
        elif "customers.id IN" in q_str or "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [1, 2]
        elif "RETURNING" in q_str:
            master_wallet.balance_paise = 750000
            mock_res.scalar_one_or_none.return_value = master_wallet
        elif "customers.dograh_org_id" in q_str and "customers.id =" in q_str:
            mock_row = MagicMock()
            mock_row.dograh_org_id = 20
            mock_row.company_name = "Sub Client A"
            mock_res.fetchone.return_value = mock_row
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_phone_number_lease_billed", AsyncMock()) as mock_notif:

        txn = await charge_initial_phone_number_lease(mock_db, customer_id=2, phone_number=sub_pn)

    assert txn is not None
    assert txn.type == TransactionType.PHONE_NUMBER_LEASE.value
    assert txn.amount_paise == -50000
    assert txn.customer_id == 1  # Master wallet owner
    assert txn.sub_org_id == 2   # Sub-org attribution
    assert txn.dograh_org_id == 20
    assert "Sub Client A" in txn.description
    assert master_wallet.balance_paise == 750000
    mock_notif.assert_awaited_once()


@pytest.mark.asyncio
async def test_recurring_monthly_lease_cron_bills_due_numbers():
    """Verify Issue 31: Daily cron bills monthly lease for active numbers exceeding free allowance when >=30 days."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old_time = now - datetime.timedelta(days=35)
    recent_time = now - datetime.timedelta(days=10)

    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, company_name="Master")
    sub_org = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, company_name="Client 1")
    master_sub = Subscription(id=1, customer_id=1, plan="starter")  # free_phone_numbers: 1
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    # 3 active numbers in fleet:
    # pn1: oldest, covered by free allowance
    pn1 = PhoneNumber(id=1, customer_id=1, number="+919876543001", status="active", purchased_at=old_time, last_billed_at=old_time)
    # pn2: 2nd number, billable, last billed 35 days ago -> DUE!
    pn2 = PhoneNumber(id=2, customer_id=2, number="+919876543002", status="active", purchased_at=old_time, last_billed_at=old_time)
    # pn3: 3rd number, billable, last billed 10 days ago -> NOT DUE
    pn3 = PhoneNumber(id=3, customer_id=2, number="+919876543003", status="active", purchased_at=recent_time, last_billed_at=recent_time)

    mock_db = MagicMock()
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_db.flush = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "from phone_numbers" in q_str.lower() and "join customers" in q_str.lower():
            mock_res.all.return_value = [
                (pn1, master),
                (pn2, sub_org),
                (pn3, sub_org),
            ]
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "RETURNING" in q_str:
            master_wallet.balance_paise = 950000
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_phone_number_lease_billed", AsyncMock()):

        results = await bill_monthly_phone_number_leases(mock_db)

    assert results["billed_count"] == 1, "Only pn2 was due and billable"
    assert results["total_paise"] == 50000
    assert results["free_numbers_count"] == 1, "pn1 was covered by free allowance"

    # pn2 last_billed_at should be refreshed to today
    assert (now - pn2.last_billed_at).total_seconds() < 5
    # pn3 last_billed_at should remain unchanged
    assert pn3.last_billed_at == recent_time


@pytest.mark.asyncio
async def test_released_phone_numbers_are_skipped_by_recurring_cron():
    """Verify Issue 31: Released phone numbers are not billed by the recurring cron."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old_time = now - datetime.timedelta(days=35)

    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None, company_name="Master")
    # A released number
    pn_released = PhoneNumber(id=1, customer_id=1, number="+919876543001", status="released", purchased_at=old_time, last_billed_at=old_time)

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "from phone_numbers" in q_str.lower():
            # Query filters by PhoneNumber.status == 'active', so returns empty
            mock_res.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    results = await bill_monthly_phone_number_leases(mock_db)
    assert results["billed_count"] == 0
    assert results["total_paise"] == 0


@pytest.mark.asyncio
async def test_release_customer_phone_number_endpoint():
    """Verify that release endpoint sets phone number status to 'released'."""
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    pn = PhoneNumber(id=10, customer_id=1, number="+919876543010", status="active")

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = pn
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    res = await release_customer_phone_number(customer_id=1, phone_number_id=10, db=mock_db, current_admin=admin)
    assert res == {"status": "released"}
    assert pn.status == "released"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fix_15_suspended_customer_phone_lease_deduction_accelerates_churn():
    """
    Fix 15: Monthly Phone Lease Billing for Suspended Fleet Accounts
    Verify that active phone numbers belonging to suspended customers are included in
    phone number lease deductions, creating a ledger debit on the master wallet.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    old_time = now - datetime.timedelta(days=35)

    suspended_master = Customer(id=1, dograh_org_id=10, status="suspended", billing_org_id=None, company_name="Suspended Master")
    pn1 = PhoneNumber(id=1, customer_id=1, number="+919876543001", status="active", purchased_at=old_time, last_billed_at=old_time)
    pn2 = PhoneNumber(id=2, customer_id=1, number="+919876543002", status="active", purchased_at=old_time, last_billed_at=old_time, monthly_cost_paise=50000)

    # Master wallet starts with low balance (e.g. 20000 paise = ₹200). After 50000 paise lease deduction, balance goes negative
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=20000, zero_balance_since=None)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()

    executed_queries = []

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        executed_queries.append(q_str)
        mock_res = MagicMock()
        if "from phone_numbers" in q_str.lower():
            # Returns both phone numbers for suspended customer
            mock_res.all.return_value = [(pn1, suspended_master), (pn2, suspended_master)]
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = Subscription(id=1, customer_id=1, plan="starter")
        elif "UPDATE wallets" in q_str or "wallets.customer_id =" in q_str or "RETURNING" in q_str:
            master_wallet.balance_paise -= 50000
            master_wallet.zero_balance_since = now
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_phone_number_lease_billed", AsyncMock()):

        results = await bill_monthly_phone_number_leases(mock_db)

    # First number is covered by free allowance (starter plan gives 1 free phone number)
    # Second number is billed at 50000 paise
    assert results["billed_count"] == 1
    assert results["total_paise"] == 50000
    assert results["free_numbers_count"] == 1

    # Verify query included Customer.status.in_(("active", "suspended"))
    lease_query = executed_queries[0]
    assert "customers.status in" in lease_query.lower()

    # Verify ledger debit WalletTransaction added
    added_txns = [call.args[0] for call in mock_db.add.call_args_list if call.args and isinstance(call.args[0], WalletTransaction)]
    assert len(added_txns) == 1
    txn = added_txns[0]
    assert txn.customer_id == 1
    assert txn.type == TransactionType.PHONE_NUMBER_LEASE.value
    assert txn.amount_paise == -50000

    # Verify Invoice was created
    added_invoices = [call.args[0] for call in mock_db.add.call_args_list if call.args and isinstance(call.args[0], Invoice)]
    assert len(added_invoices) == 1
    invoice = added_invoices[0]
    assert invoice.customer_id == 1
    assert invoice.amount_paise == 50000

    # Negative balance accelerates churn: sync_wallet_block_policy invoked
    mock_sync.assert_awaited_once_with(mock_db, 1)

