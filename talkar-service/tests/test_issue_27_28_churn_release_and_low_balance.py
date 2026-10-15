import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, PhoneNumber, Subscription, Wallet
from jobs.cron import check_suspensions, check_low_balances
from services.billing_service import bill_monthly_phone_number_leases


@pytest.mark.asyncio
async def test_blocker_27_churn_query_releases_phone_numbers():
    """Verify that churn_query includes the CTE to release phone numbers of churned accounts."""
    executed_queries = []

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    churned_row = MagicMock()
    churned_row.id = 100
    churned_row.dograh_org_id = 1000
    churned_row.contact_email = "churned@test.com"

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        executed_queries.append(q_str)
        res = MagicMock()
        if "zero_balance_since" in q_str:
            res.fetchall.return_value = []
        elif "MasterChurns" in q_str:
            res.__iter__.return_value = [churned_row]
        else:
            res.fetchall.return_value = []
            res.__iter__.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx),
        patch("jobs.cron.dograh_client.archive_org", AsyncMock()),
        patch("jobs.cron.dograh_client.release_phone_number_from_dograh", AsyncMock()),
    ):
        await check_suspensions(ctx={})

    churn_queries = [q for q in executed_queries if "MasterChurns" in q]
    assert len(churn_queries) > 0
    # Assert the CTE updates phone_numbers SET status = 'released'
    assert "UPDATE phone_numbers SET status = 'released' WHERE customer_id IN (SELECT id FROM churned_ids)" in " ".join(churn_queries[0].split())

    # Assert that phone_numbers update was included in churn_query CTE
    assert "UPDATE phone_numbers SET status = 'released'" in churn_queries[0]


@pytest.mark.asyncio
async def test_blocker_27_bill_monthly_phone_number_leases_excludes_churned_customers():
    """Verify that bill_monthly_phone_number_leases joins Customer and checks Customer.status == 'active'."""
    now = datetime.datetime.now(datetime.timezone.utc)
    old_time = now - datetime.timedelta(days=35)

    active_master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    churned_sub = Customer(id=2, dograh_org_id=20, status="churned", billing_org_id=1)

    # pn1 belongs to active master, pn2 belongs to churned sub
    pn_master = PhoneNumber(id=1, customer_id=1, number="+919876543001", status="active", purchased_at=old_time)
    pn_churned_sub = PhoneNumber(id=2, customer_id=2, number="+919876543002", status="active", purchased_at=old_time)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    executed_queries = []

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        executed_queries.append(q_str)
        mock_res = MagicMock()
        if "from phone_numbers" in q_str.lower():
            # In actual DB, the query has .where(PhoneNumber.status == 'active', Customer.status == 'active')
            # So pn_churned_sub is NOT returned by Postgres. Only active customer's numbers are returned.
            mock_res.all.return_value = [(pn_master, active_master)]
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = Subscription(id=1, customer_id=1, plan="starter")
        elif "RETURNING" in q_str:
            mock_res.scalar_one_or_none.return_value = Wallet(id=1, customer_id=1, balance_paise=1000000)
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    results = await bill_monthly_phone_number_leases(mock_db)

    # Verify query strictly joined Customer and checked Customer.status == 'active'
    lease_query = executed_queries[0]
    assert "customers.status = :status" in lease_query or "customers.status = 'active'" in lease_query
    assert "phone_numbers.status = :status" in lease_query or "phone_numbers.status = 'active'" in lease_query

    # Only master's number was processed (covered by free allowance)
    assert results["free_numbers_count"] == 1
    assert results["billed_count"] == 0


@pytest.mark.asyncio
async def test_blocker_28_low_balance_query_filters_to_billing_org_id_is_null():
    """Verify that check_low_balance_alerts filters c.billing_org_id IS NULL to notify only master wallet owners."""
    executed_queries = []

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        executed_queries.append(q_str)
        res = MagicMock()
        res.fetchall.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx):
        await check_low_balances(ctx={})

    assert len(executed_queries) > 0
    low_bal_query = executed_queries[0]
    # Blocker 28 requirement: filter query to WHERE c.billing_org_id IS NULL
    assert "c.billing_org_id IS NULL" in low_bal_query
