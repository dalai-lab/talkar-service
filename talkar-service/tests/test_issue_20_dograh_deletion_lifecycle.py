import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from sqlalchemy import text

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services import dograh_client
from jobs.cron import check_suspensions, cleanup_abandoned_signups


@pytest.mark.asyncio
async def test_delete_org_via_rest_api_success():
    """Verify delete_org calls Dograh REST API endpoint when available."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("services.dograh_client._request_dograh_api", AsyncMock(return_value=mock_resp)) as mock_req, \
         patch("services.dograh_client.DograhSessionLocal") as mock_session:
        await dograh_client.delete_org(101)

        mock_req.assert_awaited_once_with("DELETE", "/api/v1/internal/organizations/101")
        # Should not fall back to DB session if REST succeeds
        mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_delete_org_via_db_fallback():
    """Verify delete_org performs dependency-ordered SQL deletion when REST API fails."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client._request_dograh_api", AsyncMock(return_value=None)), \
         patch("services.dograh_client._has_dograh_db", return_value=True), \
         patch("services.dograh_client.DograhSessionLocal", return_value=mock_session_ctx):
        await dograh_client.delete_org(202)

    assert mock_session.execute.await_count >= 10
    executed_sqls = [str(call[0][0]) for call in mock_session.execute.call_args_list]

    # Verify dependency order:
    # 1. Nullify user selected organization
    assert any("UPDATE users SET selected_organization_id = NULL" in s for s in executed_sqls)
    # 2. Delete user association
    assert any("DELETE FROM organization_users" in s for s in executed_sqls)
    # 3. Disconnect workflow runs from campaigns and delete campaigns
    assert any("UPDATE workflow_runs SET campaign_id = NULL" in s for s in executed_sqls)
    assert any("DELETE FROM campaigns" in s for s in executed_sqls)
    # 4. Delete runs and definitions for workflows, then workflows
    assert any("DELETE FROM workflow_runs" in s for s in executed_sqls)
    assert any("DELETE FROM workflow_definitions" in s for s in executed_sqls)
    assert any("DELETE FROM workflows" in s for s in executed_sqls)
    # 5. Delete folders & integrations & usage cycles
    assert any("DELETE FROM folders" in s for s in executed_sqls)
    assert any("DELETE FROM integrations" in s for s in executed_sqls)
    assert any("DELETE FROM organization_usage_cycles" in s for s in executed_sqls)
    # 6. Delete organization
    assert any("DELETE FROM organizations WHERE id = :org_id" in s for s in executed_sqls)
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_org_via_rest_api_success():
    """Verify archive_org calls Dograh REST API endpoint when available."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("services.dograh_client._request_dograh_api", AsyncMock(return_value=mock_resp)) as mock_req, \
         patch("services.dograh_client.DograhSessionLocal") as mock_session:
        await dograh_client.archive_org(303)

        mock_req.assert_awaited_once_with("POST", "/api/v1/internal/organizations/303/archive")
        mock_session.assert_not_called()


@pytest.mark.asyncio
async def test_archive_org_via_db_fallback():
    """Verify archive_org sets capacity to 0, marks org archived, and revokes keys/numbers/workflows."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client._request_dograh_api", AsyncMock(return_value=None)), \
         patch("services.dograh_client._has_dograh_db", return_value=True), \
         patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert_config, \
         patch("services.dograh_client.DograhSessionLocal", return_value=mock_session_ctx):
        await dograh_client.archive_org(404)

    # 1. Check configs upserted
    assert mock_upsert_config.await_count == 2
    mock_upsert_config.assert_any_await(404, "CONCURRENT_CALL_LIMIT", {"value": 0})
    mock_upsert_config.assert_any_await(404, "TALKAR_ORG_STATUS", {"value": "archived"})

    # 2. Check DB operations in session
    executed_sqls = [str(call[0][0]) for call in mock_session.execute.call_args_list]
    assert any("UPDATE api_keys SET is_active = false" in s for s in executed_sqls)
    assert any("UPDATE telephony_phone_numbers SET is_active = false" in s for s in executed_sqls)
    assert any("UPDATE workflows SET status = 'archived'" in s for s in executed_sqls)
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_suspensions_churn_cascades_to_sub_orgs_and_archives():
    """Verify check_suspensions churns 45+ day suspended accounts and cascades to sub-orgs."""
    master_row = MagicMock()
    master_row.id = 501
    master_row.dograh_org_id = 9001
    master_row.contact_email = "master@test.com"

    sub_row = MagicMock()
    sub_row.id = 502
    sub_row.dograh_org_id = 9002
    sub_row.contact_email = "sub@test.com"

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "zero_balance_since" in q_str:
            # Suspend query returns empty
            res.fetchall.return_value = []
        elif "MasterChurns" in q_str or "status = 'churned'" in q_str:
            # Churn query returns both master and sub-org
            res.__iter__.return_value = [master_row, sub_row]
        else:
            res.fetchall.return_value = []
            res.__iter__.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.archive_org", AsyncMock()) as mock_archive_org, \
         patch("jobs.cron.notification_service.send_email", AsyncMock()) as mock_email:
        await check_suspensions(ctx={})

    # Both master and sub-org Dograh orgs must be archived
    assert mock_archive_org.await_count == 2
    mock_archive_org.assert_any_await(9001)
    mock_archive_org.assert_any_await(9002)

    # Both customers must receive account closure emails
    assert mock_email.await_count == 2
    mock_email.assert_any_await(
        to_email="master@test.com",
        subject="Account Closed",
        body="Your account has been permanently closed due to inactivity."
    )
    mock_email.assert_any_await(
        to_email="sub@test.com",
        subject="Account Closed",
        body="Your account has been permanently closed due to inactivity."
    )


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_invokes_delete_org():
    """Verify cleanup_abandoned_signups calls delete_org on abandoned master accounts."""
    row = MagicMock()
    row.id = 601
    row.dograh_org_id = 9999
    row.dograh_user_id = 8888

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "interval '7 days'" in q_str:
            res.__iter__.return_value = []
        elif "interval '30 days'" in q_str:
            res.__iter__.return_value = [row]
        else:
            res.__iter__.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.delete_org", AsyncMock()) as mock_delete_org:
        await cleanup_abandoned_signups(ctx={})

    mock_delete_org.assert_awaited_once_with(9999)
