import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet
from services.notification_service import (
    get_organization_report_data,
    render_report_html,
    send_organization_report,
    format_report_inr,
)


@pytest.mark.asyncio
async def test_suborg_report_data_isolates_master_wallet_balance():
    """
    Issue 16: Ensure get_organization_report_data does NOT query or leak
    the master's corporate wallet float to sub-org reports.
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,  # Belongs to master customer 1
        company_name="Client Brand A",
        contact_email="clientA@client.com",
        onboarding_form={}
    )

    db = AsyncMock()

    # Mock call metrics query and agent breakdown query
    call_row = MagicMock()
    call_row.total_calls = 100
    call_row.completed_calls = 90
    call_row.dropped_calls = 10
    call_row.total_duration_seconds = 6000
    call_row.total_cost_paise = 60000

    call_res = MagicMock()
    call_res.fetchone.return_value = call_row

    agent_res = MagicMock()
    agent_res.fetchall.return_value = []

    db.execute.side_effect = [call_res, agent_res]

    start = datetime.now(timezone.utc) - timedelta(days=7)
    end = datetime.now(timezone.utc)

    # Patch get_billing_wallet to verify it is NEVER called for sub-orgs
    with patch("services.billing_service.get_billing_wallet") as mock_get_wallet:
        data = await get_organization_report_data(
            db=db,
            customer_id=sub_org.id,
            period_start=start,
            period_end=end,
            customer=sub_org
        )

        mock_get_wallet.assert_not_called()

    assert data["is_sub_org"] is True
    assert data["current_balance_paise"] is None
    assert data["formatted_balance"] == "Managed by Master Organization"
    assert data["monthly_cap_paise"] is None
    assert data["formatted_cap"] is None
    assert data["total_calls"] == 100
    assert data["formatted_cost"] == "₹600.00"


@pytest.mark.asyncio
async def test_suborg_report_data_with_monthly_spending_cap():
    """
    Issue 16: Sub-org report data reflects configured monthly spending cap.
    """
    sub_org = Customer(
        id=21,
        billing_org_id=1,
        company_name="Client Brand B",
        contact_email="clientB@client.com",
        onboarding_form={"monthly_spending_cap_paise": 500000}  # ₹5,000 cap
    )

    db = AsyncMock()
    call_row = MagicMock()
    call_row.total_calls = 50
    call_row.completed_calls = 45
    call_row.dropped_calls = 5
    call_row.total_duration_seconds = 3000
    call_row.total_cost_paise = 30000

    call_res = MagicMock()
    call_res.fetchone.return_value = call_row

    agent_res = MagicMock()
    agent_res.fetchall.return_value = []

    db.execute.side_effect = [call_res, agent_res]

    start = datetime.now(timezone.utc) - timedelta(days=7)
    end = datetime.now(timezone.utc)

    data = await get_organization_report_data(
        db=db,
        customer_id=sub_org.id,
        period_start=start,
        period_end=end,
        customer=sub_org
    )

    assert data["is_sub_org"] is True
    assert data["current_balance_paise"] is None
    assert data["monthly_cap_paise"] == 500000
    assert data["formatted_cap"] == "₹5,000.00"


@pytest.mark.asyncio
async def test_suborg_report_data_queries_customer_when_not_passed():
    """
    Issue 16: When customer argument is None, get_organization_report_data fetches
    the customer record and correctly recognizes sub-org status.
    """
    sub_org = Customer(
        id=22,
        billing_org_id=1,
        company_name="Client Brand C",
        contact_email="clientC@client.com",
        onboarding_form={"monthly_spending_cap_paise": 1000000}
    )

    db = AsyncMock()

    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    call_res = MagicMock()
    call_res.fetchone.return_value = MagicMock(
        total_calls=0, completed_calls=0, dropped_calls=0,
        total_duration_seconds=0, total_cost_paise=0
    )

    agent_res = MagicMock()
    agent_res.fetchall.return_value = []

    # db.execute calls: 1. select Customer, 2. select call metrics, 3. select agent breakdown
    db.execute.side_effect = [cust_res, call_res, agent_res]

    start = datetime.now(timezone.utc) - timedelta(days=7)
    end = datetime.now(timezone.utc)

    with patch("services.billing_service.get_billing_wallet") as mock_get_wallet:
        data = await get_organization_report_data(
            db=db,
            customer_id=sub_org.id,
            period_start=start,
            period_end=end,
            customer=None
        )
        mock_get_wallet.assert_not_called()

    assert data["is_sub_org"] is True
    assert data["monthly_cap_paise"] == 1000000
    assert data["formatted_cap"] == "₹10,000.00"


@pytest.mark.asyncio
async def test_master_org_report_data_fetches_own_wallet_balance():
    """
    Issue 16: Master organization reports continue to include actual wallet balance.
    """
    master = Customer(
        id=1,
        billing_org_id=None,
        company_name="Master Agency",
        contact_email="owner@agency.com"
    )

    db = AsyncMock()
    call_res = MagicMock()
    call_res.fetchone.return_value = MagicMock(
        total_calls=250, completed_calls=240, dropped_calls=10,
        total_duration_seconds=15000, total_cost_paise=150000
    )

    agent_res = MagicMock()
    agent_res.fetchall.return_value = []

    db.execute.side_effect = [call_res, agent_res]

    master_wallet = Wallet(
        id=1,
        customer_id=1,
        balance_paise=25000000  # ₹2,50,000.00
    )

    start = datetime.now(timezone.utc) - timedelta(days=7)
    end = datetime.now(timezone.utc)

    with patch("services.billing_service.get_billing_wallet", new_callable=AsyncMock) as mock_get_wallet:
        mock_get_wallet.return_value = (master_wallet, 1)

        data = await get_organization_report_data(
            db=db,
            customer_id=master.id,
            period_start=start,
            period_end=end,
            customer=master
        )

        mock_get_wallet.assert_awaited_once_with(db, 1)

    assert data["is_sub_org"] is False
    assert data["current_balance_paise"] == 25000000
    assert data["formatted_balance"] == "₹250,000.00"


def test_render_report_html_suborg_does_not_leak_master_balance():
    """
    Issue 16: Sub-org HTML digest masks master float, renders Central Master Account label,
    and links to /dashboard instead of /wallet.
    """
    data = {
        "is_sub_org": True,
        "current_balance_paise": None,
        "formatted_balance": "Managed by Master Organization",
        "monthly_cap_paise": None,
        "formatted_cap": None,
        "total_calls": 85,
        "completed_calls": 80,
        "dropped_calls": 5,
        "completion_rate": 94.1,
        "total_duration_sec": 5100,
        "formatted_duration": "1h 25m",
        "avg_duration_sec": 60,
        "formatted_avg_duration": "1m",
        "total_cost_paise": 51000,
        "formatted_cost": "₹510.00",
        "agents": []
    }

    html = render_report_html(
        data=data,
        company_name="Acme Client",
        period_label="Weekly",
        date_range_str="Oct 07, 2026 – Oct 14, 2026"
    )

    # Master corporate balance must not leak
    assert "₹250,000.00" not in html
    assert "Wallet Balance:" not in html

    # Sub-org specific accounting context
    assert "Billing: Central Master Account" in html
    assert "https://talkar.in/dashboard" in html
    assert "View Live Analytics" in html
    assert "https://talkar.in/wallet" not in html


def test_render_report_html_suborg_with_cap_renders_monthly_cap():
    """
    Issue 16: Sub-org HTML digest displays monthly spending cap when configured.
    """
    data = {
        "is_sub_org": True,
        "current_balance_paise": None,
        "formatted_balance": "Managed by Master Organization",
        "monthly_cap_paise": 750000,
        "formatted_cap": "₹7,500.00",
        "total_calls": 40,
        "completed_calls": 38,
        "dropped_calls": 2,
        "completion_rate": 95.0,
        "total_duration_sec": 2400,
        "formatted_duration": "40m",
        "avg_duration_sec": 60,
        "formatted_avg_duration": "1m",
        "total_cost_paise": 24000,
        "formatted_cost": "₹240.00",
        "agents": []
    }

    html = render_report_html(
        data=data,
        company_name="Acme Client",
        period_label="Weekly",
        date_range_str="Oct 07, 2026 – Oct 14, 2026"
    )

    assert "Monthly Cap: ₹7,500.00" in html
    assert "Wallet Balance:" not in html
    assert "https://talkar.in/dashboard" in html


def test_render_report_html_master_shows_actual_wallet_balance():
    """
    Issue 16: Master organization HTML digest shows full wallet balance and wallet link.
    """
    data = {
        "is_sub_org": False,
        "current_balance_paise": 25000000,
        "formatted_balance": "₹250,000.00",
        "monthly_cap_paise": None,
        "formatted_cap": None,
        "total_calls": 500,
        "completed_calls": 480,
        "dropped_calls": 20,
        "completion_rate": 96.0,
        "total_duration_sec": 30000,
        "formatted_duration": "8h 20m",
        "avg_duration_sec": 60,
        "formatted_avg_duration": "1m",
        "total_cost_paise": 300000,
        "formatted_cost": "₹3,000.00",
        "agents": []
    }

    html = render_report_html(
        data=data,
        company_name="Agency Master",
        period_label="Weekly",
        date_range_str="Oct 07, 2026 – Oct 14, 2026"
    )

    assert "Wallet Balance: ₹250,000.00" in html
    assert "https://talkar.in/wallet" in html
    assert "View Live Analytics & Wallet" in html


@pytest.mark.asyncio
async def test_send_organization_report_dispatches_sanitized_suborg_digest():
    """
    Issue 16: send_organization_report passes customer object down and dispatches
    digest free of master wallet float.
    """
    sub_org = Customer(
        id=30,
        billing_org_id=1,
        company_name="SubOrg Brand",
        contact_email="suborg@client.com",
        report_settings={"enabled": True, "frequency": "weekly", "recipients": ["suborg@client.com"]},
        onboarding_form={"monthly_spending_cap_paise": 200000}
    )

    db = AsyncMock()
    call_res = MagicMock()
    call_res.fetchone.return_value = MagicMock(
        total_calls=10, completed_calls=9, dropped_calls=1,
        total_duration_seconds=600, total_cost_paise=6000
    )
    agent_res = MagicMock()
    agent_res.fetchall.return_value = []
    db.execute.side_effect = [call_res, agent_res]

    with patch("services.notification_service.settings") as mock_settings:
        mock_settings.SMTP_HOST = ""  # Mock mode
        mock_settings.SMTP_PASSWORD = ""
        mock_settings.FROM_EMAIL = "reports@talkar.in"

        sent = await send_organization_report(
            db=db,
            customer=sub_org,
            frequency="weekly",
            is_test=False
        )

    assert sent is True
