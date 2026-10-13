import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from jobs.cron import nightly_reconciliation
import services.billing_service as billing_service
import services.dograh_client as dograh_client


@pytest.mark.asyncio
async def test_get_completed_runs_query_filters_textchat():
    """get_completed_runs should include (wfr.mode IS NULL OR wfr.mode != 'textchat') in its SQL query."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session.execute.return_value = mock_result

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_session_ctx):
        runs = await dograh_client.get_completed_runs(hours=25)

    assert runs == []
    mock_session.execute.assert_awaited_once()
    sql_text = str(mock_session.execute.call_args[0][0])
    assert "(wfr.mode IS NULL OR wfr.mode != 'textchat')" in sql_text


@pytest.mark.asyncio
async def test_nightly_reconciliation_skips_textchat_runs():
    """nightly_reconciliation should skip textchat runs and only deduct billable telephony/webrtc runs."""
    mock_runs = [
        {"id": 101, "organization_id": 1, "workflow_id": 10, "mode": "telephony", "duration_seconds": 120.0},
        {"id": 102, "organization_id": 1, "workflow_id": 10, "mode": "textchat", "duration_seconds": 0.0},
        {"id": 103, "organization_id": 1, "workflow_id": 10, "mode": "textchat", "duration_seconds": 45.0},
        {"id": 104, "organization_id": 1, "workflow_id": 10, "mode": "webrtc", "duration_seconds": 60.0},
    ]

    mock_db = MagicMock()
    mock_result = MagicMock()
    # Assume none have been processed yet
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.get_completed_runs", AsyncMock(return_value=mock_runs)), \
         patch("jobs.cron.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("services.billing_service.deduct_for_run", AsyncMock()) as mock_deduct:
        await nightly_reconciliation(ctx={})

    # Only run 101 (telephony) and run 104 (webrtc) should be processed
    assert mock_deduct.await_count == 2
    mock_deduct.assert_any_await(101)
    mock_deduct.assert_any_await(104)

    # 102 and 103 (textchat) must never be passed to deduct_for_run
    called_run_ids = [call.args[0] for call in mock_deduct.await_args_list]
    assert 102 not in called_run_ids
    assert 103 not in called_run_ids


@pytest.mark.asyncio
async def test_deduct_for_run_skips_textchat_mode():
    """services.billing_service.deduct_for_run should immediately skip textchat runs without querying DB."""
    textchat_run = {
        "id": 999,
        "organization_id": 1,
        "workflow_id": 10,
        "mode": "textchat",
        "duration_seconds": 0.0,
    }

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.get_run", AsyncMock(return_value=textchat_run)), \
         patch("db.session.AsyncSessionLocal", return_value=mock_session_ctx):
        await billing_service.deduct_for_run(run_id=999)

    # Should not execute any queries on Talkar DB for textchat
    mock_db.execute.assert_not_called()
    mock_db.commit.assert_not_called()
