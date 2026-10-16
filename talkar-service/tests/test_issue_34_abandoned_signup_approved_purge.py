import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from jobs.cron import cleanup_abandoned_signups


def test_abandoned_signup_query_structure():
    """Verify Issue 34: cleanup_abandoned_signups query includes 'approved' status."""
    import inspect
    from jobs.cron import cleanup_abandoned_signups
    source = inspect.getsource(cleanup_abandoned_signups)
    assert "status IN ('pending_approval', 'approved')" in source


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_purges_approved_master_and_sub_org_dograh_workspaces():
    """
    Verify Issue 34:
    1. An abandoned master in 'approved' status (> 30 days) is picked up and deleted.
    2. Child sub-organizations in 'pending_approval', 'under_review', or 'approved'
       have their Dograh workspaces deleted via dograh_client.delete_org.
    3. Child sub-org status is set to 'churned' and billing_org_id set to None.
    """
    master_row = MagicMock()
    master_row.id = 201
    master_row.dograh_org_id = 2001
    master_row.dograh_user_id = 1001

    sub_org_1 = Customer(
        id=301,
        dograh_org_id=3001,
        billing_org_id=201,
        status="under_review"
    )
    sub_org_2 = Customer(
        id=302,
        dograh_org_id=3002,
        billing_org_id=201,
        status="approved"
    )
    sub_org_3 = Customer(
        id=303,
        dograh_org_id=None,
        billing_org_id=201,
        status="pending_approval"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "interval '7 days'" in q_str:
            res.__iter__.return_value = []
            return res
        elif "interval '30 days'" in q_str:
            res.__iter__.return_value = [master_row]
            return res
        elif "SELECT 1 FROM customers" in q_str:
            # Active child sub-org guard check: return empty so master is purged
            res.__iter__.return_value = []
            return res
        elif "SELECT customers.id" in q_str or "customers.billing_org_id =" in q_str:
            # sub_orgs query
            res.scalars.return_value.all.return_value = [sub_org_1, sub_org_2, sub_org_3]
            return res
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

    # Master Dograh org deleted
    mock_delete_org.assert_any_await(2001)

    # Sub-org Dograh orgs deleted (sub_org_1 and sub_org_2 had dograh_org_ids)
    mock_delete_org.assert_any_await(3001)
    mock_delete_org.assert_any_await(3002)
    assert mock_delete_org.await_count == 3

    # Verify sub-orgs archived and detached
    assert sub_org_1.status == "churned"
    assert sub_org_1.billing_org_id is None
    assert sub_org_2.status == "churned"
    assert sub_org_2.billing_org_id is None
    assert sub_org_3.status == "churned"
    assert sub_org_3.billing_org_id is None

    # Verify master deletion SQL executed
    executed_sqls = [str(call[0][0]) for call in mock_db.execute.call_args_list]
    assert any("DELETE FROM customers WHERE id = :id" in s for s in executed_sqls)
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_handles_sub_org_dograh_deletion_failure_gracefully():
    """Verify Issue 34: If dograh_client.delete_org fails on a child sub-org, process continues."""
    master_row = MagicMock()
    master_row.id = 202
    master_row.dograh_org_id = 2002
    master_row.dograh_user_id = 1002

    failing_sub_org = Customer(
        id=305,
        dograh_org_id=3005,
        billing_org_id=202,
        status="under_review"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "interval '7 days'" in q_str:
            res.__iter__.return_value = []
            return res
        elif "interval '30 days'" in q_str:
            res.__iter__.return_value = [master_row]
            return res
        elif "SELECT 1 FROM customers" in q_str:
            res.__iter__.return_value = []
            return res
        elif "SELECT customers.id" in q_str or "customers.billing_org_id =" in q_str:
            res.scalars.return_value.all.return_value = [failing_sub_org]
            return res
        else:
            res.__iter__.return_value = []
            return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    async def mock_delete_org_side_effect(org_id):
        if org_id == 3005:
            raise RuntimeError("Dograh API temporary error on sub-org")
        return {"status": "deleted"}

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.delete_org", AsyncMock(side_effect=mock_delete_org_side_effect)) as mock_del:
        # Should not raise exception
        await cleanup_abandoned_signups(ctx={})

    assert mock_del.await_count == 2
    assert failing_sub_org.status == "churned"
    assert failing_sub_org.billing_org_id is None

    executed_sqls = [str(call[0][0]) for call in mock_db.execute.call_args_list]
    assert any("DELETE FROM customers WHERE id = :id" in s for s in executed_sqls)
    mock_db.commit.assert_awaited()
