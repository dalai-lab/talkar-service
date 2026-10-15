import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, PhoneNumber, Subscription, Wallet
from services import notification_service
from services.billing_service import bill_monthly_phone_number_leases
from jobs.cron import check_suspensions
from routers.customers import get_customer_status


@pytest.mark.asyncio
async def test_blocker_19_churn_releases_dids_and_notifies_dograh():
    """
    Test Blocker 19:
    When an account churns in cron check_suspensions, all associated phone numbers
    are updated to status='released' and released from Dograh telephony.
    """
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    # Step 1: Churn query returns 1 churned customer
    row_churned = MagicMock(id=42, dograh_org_id=999, contact_email="churned@example.com")
    res_churn = MagicMock()
    res_churn.__iter__.return_value = [row_churned]

    # Step 2: Query for phone numbers of churned customer
    pn1 = PhoneNumber(id=1, customer_id=42, number="+919876543210", status="active")
    pn2 = PhoneNumber(id=2, customer_id=42, number="+919876543211", status="active")
    res_pns = MagicMock()
    res_pns.scalars.return_value.all.return_value = [pn1, pn2]

    # Empty zero balance check for the first query in check_suspensions
    res_zero = MagicMock()
    res_zero.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [res_zero, res_churn, res_pns]

    with patch("jobs.cron.AsyncSessionLocal") as mock_session_ctx, \
         patch("services.dograh_client.archive_org", AsyncMock()) as mock_archive, \
         patch("services.dograh_client.release_phone_number_from_dograh", AsyncMock()) as mock_release_pn, \
         patch("services.notification_service.send_email", AsyncMock()):

        mock_session_ctx.return_value.__aenter__.return_value = mock_db

        await check_suspensions(None)

    # Verify DIDs status set to 'released'
    assert pn1.status == "released"
    assert pn2.status == "released"

    # Verify Dograh release called for both numbers
    assert mock_release_pn.call_count == 2
    mock_release_pn.assert_any_call(999, "+919876543210")
    mock_release_pn.assert_any_call(999, "+919876543211")


@pytest.mark.asyncio
async def test_blocker_19_lease_billing_skips_inactive_customers():
    """
    Test Blocker 19:
    bill_monthly_phone_number_leases only evaluates active phone numbers
    where Customer.status == 'active'.
    """
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    # Query returns empty because join filter Customer.status == 'active' excludes churned/suspended
    res_empty = MagicMock()
    res_empty.all.return_value = []
    mock_db.execute.return_value = res_empty

    result = await bill_monthly_phone_number_leases(mock_db)
    assert result["billed_count"] == 0
    assert result["total_paise"] == 0

    # Inspect the SQL executed: contains customers.status = :status_1 or similar
    call_query = mock_db.execute.call_args[0][0]
    compiled_sql = str(call_query)
    assert "customers.status" in compiled_sql
    assert "phone_numbers.status" in compiled_sql


@pytest.mark.asyncio
async def test_blocker_20_notify_customer_suspended_reason_aware_messaging():
    """
    Test Blocker 20:
    notify_customer_suspended differentiates fraud/policy violations (contact support)
    from zero balance (top up).
    """
    with patch("services.notification_service._get_customer_email", AsyncMock(return_value=("user@test.com", "Test User"))), \
         patch("services.notification_service.send_email_and_push", AsyncMock()) as mock_send:

        # 1. Zero balance suspension
        await notification_service.notify_customer_suspended(customer_id=1, reason="zero_balance")
        call_zb = mock_send.call_args.kwargs
        assert "top up your wallet" in call_zb["body"]
        assert "Top up to reactivate." in call_zb["push_body"]

        mock_send.reset_mock()

        # 2. Fraud suspension
        await notification_service.notify_customer_suspended(customer_id=1, reason="fraud")
        call_fraud = mock_send.call_args.kwargs
        assert "support@talkar.in" in call_fraud["body"]
        assert "top up your wallet" not in call_fraud["body"]
        assert "Contact support for assistance." in call_fraud["push_body"]

        mock_send.reset_mock()

        # 3. Policy violation suspension
        await notification_service.notify_customer_suspended(customer_id=1, reason="policy_violation")
        call_policy = mock_send.call_args.kwargs
        assert "support@talkar.in" in call_policy["body"]
        assert "top up your wallet" not in call_policy["body"]
        assert "Contact support for assistance." in call_policy["push_body"]


@pytest.mark.asyncio
async def test_blocker_20_status_endpoint_returns_master_suspension_reason_to_suborg():
    """
    Test Blocker 20:
    get_customer_status_by_org returns the master's suspension_reason if the sub-org
    has no local suspension_reason in its onboarding_form.
    """
    sub_org = Customer(
        id=2,
        dograh_org_id=200,
        billing_org_id=1,
        status="suspended",
        onboarding_form={}
    )
    master = Customer(
        id=1,
        dograh_org_id=100,
        billing_org_id=None,
        status="suspended",
        onboarding_form={"suspension_reason": "fraud", "suspension_message": "Flagged for abusive traffic"}
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()

    # Query 1: lookup sub_org
    res_sub = MagicMock()
    res_sub.scalars.return_value.first.return_value = sub_org

    # Query 2: Subscription lookup
    res_sub_plan = MagicMock()
    res_sub_plan.scalar_one_or_none.return_value = Subscription(customer_id=1, plan="starter")

    # Query 3: Wallet lookup
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = Wallet(customer_id=1, balance_paise=0)

    # Query 4: Master customer lookup for suspension_reason fallback
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    mock_db.execute.side_effect = [res_sub, res_sub_plan, res_wallet, res_master]

    with patch("routers.customers.verify_customer_access", AsyncMock()):
        resp = await get_customer_status(dograh_org_id=200, db=mock_db)

    assert resp["status"] == "suspended"
    assert resp["suspension_reason"] == "fraud"
    assert resp["suspension_message"] == "Flagged for abusive traffic"
