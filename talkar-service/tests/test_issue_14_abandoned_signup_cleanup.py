import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from sqlalchemy import text

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from jobs.cron import cleanup_abandoned_signups


def test_customer_billing_org_id_foreign_key_has_ondelete_set_null():
    """Verify Issue 14: Customer.billing_org_id defines ForeignKey with ondelete='SET NULL'."""
    col = Customer.billing_org_id.property.columns[0]
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.target_fullname == "customers.id"
    assert fk.ondelete.upper() == "SET NULL"


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_detaches_sub_orgs_and_deletes_all_child_records():
    """Verify Issue 14: cleanup_abandoned_signups detaches sub-orgs and cleans up notifications, invoices, and customer."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    # Day 7 reminders: empty
    res_reminders = MagicMock()
    res_reminders.__iter__.return_value = []

    # Day 30 abandoned: 1 master customer
    row = MagicMock()
    row.id = 101
    row.dograh_org_id = 999
    row.dograh_user_id = 888

    res_abandoned = MagicMock()
    res_abandoned.__iter__.return_value = [row]

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

    mock_delete_org.assert_awaited_once_with(999)

    # Collect all queries executed
    executed_sqls = [str(call[0][0]) for call in mock_db.execute.call_args_list]

    # Verify sub-org detachment SQL executed before deleting customer
    detach_found = any("UPDATE customers SET billing_org_id = NULL WHERE billing_org_id = :id" in s for s in executed_sqls)
    assert detach_found, "Must detach sub-orgs via UPDATE customers SET billing_org_id = NULL"

    # Verify notifications and invoices deleted
    assert any("DELETE FROM notifications WHERE customer_id = :id" in s for s in executed_sqls)
    assert any("DELETE FROM invoices WHERE customer_id = :id" in s for s in executed_sqls)

    # Verify customer deleted
    assert any("DELETE FROM customers WHERE id = :id" in s for s in executed_sqls)

    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_handles_single_customer_error_without_crashing_loop():
    """Verify Issue 14: Error on one abandoned customer rolls back that customer but proceeds with remaining customers."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    # Two abandoned customers: row1 fails, row2 succeeds
    row1 = MagicMock(id=10, dograh_org_id=100, dograh_user_id=1)
    row2 = MagicMock(id=20, dograh_org_id=200, dograh_user_id=2)

    def side_effect_execute(query, params=None, *args, **kwargs):
        q_str = str(query)
        if "interval '7 days'" in q_str:
            res = MagicMock()
            res.__iter__.return_value = []
            return res
        elif "interval '30 days'" in q_str:
            res = MagicMock()
            res.__iter__.return_value = [row1, row2]
            return res
        
        # Simulate unexpected failure when deleting row1's customer record
        if "DELETE FROM customers WHERE id = :id" in q_str and params and params.get("id") == 10:
            raise RuntimeError("Simulated DB error on customer 10")

        res = MagicMock()
        res.__iter__.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=side_effect_execute)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("jobs.cron.dograh_client.delete_org", AsyncMock()):
        # Should not raise exception
        await cleanup_abandoned_signups(ctx={})

    # Rolled back for row1
    mock_db.rollback.assert_awaited_once()

    # row2 was successfully processed and committed
    executed_sqls_with_params = [
        (str(call[0][0]), call[0][1] if len(call[0]) > 1 else {})
        for call in mock_db.execute.call_args_list
    ]
    row2_deleted = any(
        "DELETE FROM customers WHERE id = :id" in s and p.get("id") == 20
        for s, p in executed_sqls_with_params
    )
    assert row2_deleted, "Row 2 must be deleted even if Row 1 encountered an error"


@pytest.mark.asyncio
async def test_cleanup_abandoned_signups_survives_dograh_client_exception():
    """Verify Issue 14: If dograh_client.delete_org fails (network/404), DB cleanup continues."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    row = MagicMock(id=50, dograh_org_id=500, dograh_user_id=5)

    def side_effect_execute(query, params=None, *args, **kwargs):
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
         patch("jobs.cron.dograh_client.delete_org", AsyncMock(side_effect=Exception("Dograh unreachable"))):
        await cleanup_abandoned_signups(ctx={})

    # DB cleanup should have proceeded despite dograh client error
    executed_sqls = [str(call[0][0]) for call in mock_db.execute.call_args_list]
    assert any("DELETE FROM customers WHERE id = :id" in s for s in executed_sqls)
    mock_db.commit.assert_awaited()
