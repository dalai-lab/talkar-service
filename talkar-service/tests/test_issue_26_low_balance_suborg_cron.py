import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs.cron import check_low_balances


class MockRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_check_low_balances_alerts_sub_organizations():
    """Verify Issue 26: Active sub-organizations under a shared wallet receive low-balance alerts."""
    now_utc = datetime.now(timezone.utc)
    # Master row
    row_master = MockRow(
        customer_id=1,
        billing_org_id=None,
        contact_email="agency@example.com",
        report_settings={},
        wallet_customer_id=1,
        balance_paise=100000,  # ₹1,000 (below ₹1,500 default)
        low_balance_alerted_at=None,
    )
    # Sub-org row
    row_sub = MockRow(
        customer_id=2,
        billing_org_id=1,
        contact_email="sub1@example.com",
        report_settings={"cc_email": "finance@sub1.com"},
        wallet_customer_id=1,
        balance_paise=100000,  # ₹1,000 (shared wallet)
        low_balance_alerted_at=None,
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row_master, row_sub])))
    mock_db.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_cm), \
         patch("services.notification_service.send_email", AsyncMock()) as mock_send_email:

        await check_low_balances(None)

        # Both master and sub-org must be alerted
        assert mock_send_email.call_count == 2
        emails_sent = [call.kwargs.get("to_email") for call in mock_send_email.call_args_list]
        assert "agency@example.com" in emails_sent
        assert "sub1@example.com" in emails_sent

        # Verify sub-org cc email was passed
        sub_call = [call for call in mock_send_email.call_args_list if call.kwargs.get("to_email") == "sub1@example.com"][0]
        assert sub_call.kwargs.get("cc") == "finance@sub1.com"


@pytest.mark.asyncio
async def test_check_low_balances_respects_sub_org_custom_threshold():
    """Verify Issue 26: Sub-org with a higher threshold (₹3,000) is alerted when master (₹1,500) is not."""
    # Master has default threshold ₹1,500; wallet has ₹2,000 -> Master not alerted
    row_master = MockRow(
        customer_id=1,
        billing_org_id=None,
        contact_email="agency@example.com",
        report_settings={"low_balance_alert_paise": 150000},
        wallet_customer_id=1,
        balance_paise=200000,  # ₹2,000
        low_balance_alerted_at=None,
    )
    # Sub-org has custom threshold ₹3,000 -> Sub-org MUST be alerted
    row_sub = MockRow(
        customer_id=2,
        billing_org_id=1,
        contact_email="sub1@example.com",
        report_settings={"low_balance_alert_paise": 300000},
        wallet_customer_id=1,
        balance_paise=200000,  # ₹2,000
        low_balance_alerted_at=None,
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row_master, row_sub])))
    mock_db.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_cm), \
         patch("services.notification_service.send_email", AsyncMock()) as mock_send_email:

        await check_low_balances(None)

        # Only sub-org must be alerted
        assert mock_send_email.call_count == 1
        call_kwargs = mock_send_email.call_args.kwargs
        assert call_kwargs["to_email"] == "sub1@example.com"


@pytest.mark.asyncio
async def test_check_low_balances_respects_sub_org_muted_email_preference():
    """Verify Issue 26: Sub-org with email_notifications_enabled=False gets in-app push only."""
    row_sub = MockRow(
        customer_id=2,
        billing_org_id=1,
        contact_email="sub1@example.com",
        report_settings={"email_notifications_enabled": False, "low_balance_alert_paise": 150000},
        wallet_customer_id=1,
        balance_paise=100000,
        low_balance_alerted_at=None,
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row_sub])))
    mock_db.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_cm), \
         patch("services.notification_service.send_email", AsyncMock()) as mock_send_email, \
         patch("services.notification_service.push_notification", AsyncMock()) as mock_push:

        await check_low_balances(None)

        assert mock_send_email.call_count == 0
        assert mock_push.call_count == 1
        push_kwargs = mock_push.call_args.kwargs
        assert push_kwargs["customer_id"] == 2
        assert "Low Balance Alert" in push_kwargs["title"]


@pytest.mark.asyncio
async def test_check_low_balances_sub_org_24h_rate_limiting():
    """Verify Issue 26: Sub-org alerted <24h ago is rate-limited, but sibling sub-org is alerted."""
    now_utc = datetime.now(timezone.utc)
    recent_alert = (now_utc - timedelta(hours=2)).isoformat()

    row_sub1 = MockRow(
        customer_id=2,
        billing_org_id=1,
        contact_email="sub1@example.com",
        report_settings={"low_balance_alerted_at": recent_alert},
        wallet_customer_id=1,
        balance_paise=100000,
        low_balance_alerted_at=None,
    )
    row_sub2 = MockRow(
        customer_id=3,
        billing_org_id=1,
        contact_email="sub2@example.com",
        report_settings={},
        wallet_customer_id=1,
        balance_paise=100000,
        low_balance_alerted_at=None,
    )

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[row_sub1, row_sub2])))
    mock_db.commit = AsyncMock()

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_cm), \
         patch("services.notification_service.send_email", AsyncMock()) as mock_send_email:

        await check_low_balances(None)

        # Only sub2 should receive the email
        assert mock_send_email.call_count == 1
        assert mock_send_email.call_args.kwargs["to_email"] == "sub2@example.com"
