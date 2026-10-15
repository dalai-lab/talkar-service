import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from db.models import Customer, Subscription, Wallet
from jobs.cron import check_suspensions
from services import dograh_client


@pytest.mark.asyncio
async def test_blocker_26_churn_query_uses_coalesce_suspended_at():
    """Verify that churn_query evaluates COALESCE(suspended_at, updated_at) < now() - interval '45 days'."""
    executed_queries = []
    
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        executed_queries.append(q_str)
        res = MagicMock()
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
        patch("jobs.cron.dograh_client.block_org_calls", AsyncMock()),
        patch("jobs.cron.notification_service.notify_customer_suspended", AsyncMock()),
    ):
        await check_suspensions(ctx={})

    # Assert that suspend_query sets suspended_at = now()
    suspend_queries = [q for q in executed_queries if "MasterSuspensions" in q]
    assert len(suspend_queries) > 0
    assert "suspended_at = now()" in suspend_queries[0]

    # Assert that churn_query evaluates COALESCE(suspended_at, updated_at) < now() - interval '45 days'
    churn_queries = [q for q in executed_queries if "MasterChurns" in q]
    assert len(churn_queries) > 0
    assert "COALESCE(suspended_at, updated_at) < now() - interval '45 days'" in churn_queries[0]


@pytest.mark.asyncio
async def test_blocker_26_administrative_edits_do_not_postpone_churn():
    """
    Verify that an account suspended > 45 days ago is captured by churn even if
    updated_at was refreshed recently by administrative edits.
    """
    now = datetime.now(timezone.utc)
    old_suspended_at = now - timedelta(days=50)
    recent_updated_at = now - timedelta(days=2)

    # Master customer suspended 50 days ago, but updated_at touched 2 days ago
    cust = Customer(
        id=10,
        status="suspended",
        suspended_at=old_suspended_at,
        updated_at=recent_updated_at,
        dograh_org_id=1001,
        billing_org_id=None,
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    churned_row = MagicMock()
    churned_row.id = cust.id
    churned_row.dograh_org_id = cust.dograh_org_id
    churned_row.contact_email = "test@example.com"

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "zero_balance_since" in q_str:
            res.fetchall.return_value = []
        elif "MasterChurns" in q_str:
            # Emulating database matching COALESCE(suspended_at, updated_at) < now() - 45 days
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
        patch("jobs.cron.dograh_client.archive_org", AsyncMock()) as mock_archive,
    ):
        await check_suspensions(ctx={})

    mock_archive.assert_awaited_once_with(1001)


@pytest.mark.asyncio
async def test_blocker_25_dograh_client_invalidate_concurrency_group():
    """Verify dograh_client.invalidate_dograh_concurrency_group calls Dograh REST API."""
    with patch("services.dograh_client._request_dograh_api", AsyncMock()) as mock_req:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_req.return_value = mock_resp

        await dograh_client.invalidate_dograh_concurrency_group(999)

        mock_req.assert_awaited_once_with(
            "POST",
            "/api/v1/internal/concurrency-group/999/invalidate",
        )
