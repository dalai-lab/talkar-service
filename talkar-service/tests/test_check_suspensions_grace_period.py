import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs.cron import check_suspensions


@pytest.mark.asyncio
async def test_check_suspensions_query_uses_zero_balance_since_grace_period():
    """Verify that check_suspensions evaluates zero_balance_since instead of any positive top-up."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_db.execute.return_value = mock_result
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx):
        await check_suspensions(ctx={})

    mock_db.execute.assert_awaited()
    # Check the initial suspend_query SQL executed
    first_call_sql = str(mock_db.execute.call_args_list[0][0][0])
    
    # Must check balance_paise <= 0
    assert "w.balance_paise <= 0" in first_call_sql
    # Must use zero_balance_since to honor the 14-day continuous zero balance grace period
    assert "w.zero_balance_since" in first_call_sql
    assert "interval '14 days'" in first_call_sql
    # Must NOT use the old buggy clause that checks any positive transaction in last 14 days
    assert "wt.amount_paise > 0" not in first_call_sql


@pytest.mark.asyncio
async def test_check_suspensions_suspends_overdue_and_triggers_side_effects():
    """Verify that customers meeting the 14-day zero balance threshold are suspended and blocked."""
    mock_row = MagicMock()
    mock_row.id = 42
    mock_row.dograh_org_id = 99
    mock_row.contact_email = "test@example.com"
    mock_row.onboarding_form = {"company_name": "Acme"}

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_db.execute.return_value = mock_result
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.block_org_calls", AsyncMock()) as mock_block_calls, \
         patch("jobs.cron.notification_service.notify_customer_suspended", AsyncMock()) as mock_notify:
        await check_suspensions(ctx={})

    # Assert Dograh org blocked with reason zero_balance
    mock_block_calls.assert_awaited_once_with(99)
    mock_notify.assert_awaited_once_with(42, reason="zero_balance")

    # Assert DB commit called
    assert mock_db.commit.await_count >= 2


@pytest.mark.asyncio
async def test_zero_balance_grace_period_logic_simulation():
    """Simulate the 14-day grace period logic for a customer topping up on Day 1 and draining on Day 20."""
    now = datetime.now(timezone.utc)
    
    # Day 1: Top up ₹1000 -> zero_balance_since is None
    zero_balance_since = None
    balance_paise = 100000

    # Day 20: Drained to 0 -> zero_balance_since stamped as Day 20 (now - 1 day)
    day_20 = now - timedelta(days=1)
    zero_balance_since = day_20
    balance_paise = 0

    # Cron runs at Day 21 (1 day after reaching 0)
    is_overdue = balance_paise <= 0 and (zero_balance_since <= now - timedelta(days=14))
    assert not is_overdue, "Customer should NOT be suspended after only 1 day at ₹0 despite last top-up being 20 days ago!"

    # Cron runs at Day 35 (15 days after reaching 0)
    zero_balance_since = now - timedelta(days=15)
    is_overdue = balance_paise <= 0 and (zero_balance_since <= now - timedelta(days=14))
    assert is_overdue, "Customer SHOULD be suspended after 15 continuous days at ₹0"
