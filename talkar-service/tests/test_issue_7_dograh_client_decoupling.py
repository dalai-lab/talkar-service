import sys
from pathlib import Path
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
import services.dograh_client as dograh_client


@pytest.mark.asyncio
async def test_upsert_org_config_uses_rest_api():
    """Verify upsert_org_config calls Dograh internal REST API when available."""
    mock_response = MagicMock(status_code=200)
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        await dograh_client.upsert_org_config(
            org_id=101,
            key="CONCURRENT_CALL_LIMIT",
            value={"value": 5}
        )
        
        assert mock_request.called
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert "/api/v1/internal/org-config/101" in call_args[0][1]
        assert call_args[1]["json"] == {"key": "CONCURRENT_CALL_LIMIT", "value": {"value": 5}}
        headers = call_args[1]["headers"]
        assert "X-Service-Token" in headers
        assert "Authorization" in headers


@pytest.mark.asyncio
async def test_get_org_config_uses_rest_api():
    """Verify get_org_config retrieves configuration via Dograh internal REST API."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {
        "org_id": 101,
        "key": "CONCURRENT_CALL_LIMIT",
        "value": {"value": 10}
    }
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        val = await dograh_client.get_org_config(101, "CONCURRENT_CALL_LIMIT")
        
        assert val == {"value": 10}
        assert mock_request.called
        call_args = mock_request.call_args
        assert call_args[0][0] == "GET"
        assert "/api/v1/internal/org-config/101/CONCURRENT_CALL_LIMIT" in call_args[0][1]


@pytest.mark.asyncio
async def test_get_run_uses_rest_api():
    """Verify get_run retrieves run metadata via Dograh internal REST API."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {
        "id": 999,
        "organization_id": 101,
        "workflow_id": 55,
        "status": "completed",
        "mode": "webrtc",
        "duration_seconds": 124.5,
    }
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        run = await dograh_client.get_run(999)
        
        assert run is not None
        assert run["id"] == 999
        assert run["duration_seconds"] == 124.5
        assert run["organization_id"] == 101
        assert run["status"] == "completed"
        call_args = mock_request.call_args
        assert "/api/v1/internal/runs/999" in call_args[0][1]


@pytest.mark.asyncio
async def test_get_run_404_returns_none():
    """Verify get_run returns None when Dograh returns 404."""
    mock_response = MagicMock(status_code=404)
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)):
        run = await dograh_client.get_run(999999)
        assert run is None


@pytest.mark.asyncio
async def test_get_completed_runs_uses_rest_api():
    """Verify get_completed_runs retrieves completed runs via Dograh internal REST API."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = [
        {"id": 1, "organization_id": 10, "duration_seconds": 60.0},
        {"id": 2, "organization_id": 20, "duration_seconds": 120.0},
    ]
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        runs = await dograh_client.get_completed_runs(hours=12)
        
        assert len(runs) == 2
        assert runs[0]["id"] == 1
        call_args = mock_request.call_args
        assert "/api/v1/internal/runs/completed" in call_args[0][1]
        assert call_args[1]["params"] == {"hours": 12}


@pytest.mark.asyncio
async def test_verify_dograh_org_user_uses_rest_api():
    """Verify verify_dograh_org_user queries Dograh internal REST API."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"verified": True}
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        is_verified = await dograh_client.verify_dograh_org_user(
            org_id=101,
            user_id=42,
            email="user@example.com"
        )
        
        assert is_verified is True
        call_args = mock_request.call_args
        assert "/api/v1/internal/org-users/verify" in call_args[0][1]
        assert call_args[1]["params"] == {
            "org_id": 101,
            "user_id": 42,
            "email": "user@example.com",
            "require_owner": False,
        }


@pytest.mark.asyncio
async def test_sync_phone_number_uses_rest_api():
    """Verify sync_phone_number_to_dograh delegates to Dograh internal REST API."""
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"status": "success", "phone_number": "+919876543210"}
    
    with patch("httpx.AsyncClient.request", AsyncMock(return_value=mock_response)) as mock_request:
        await dograh_client.sync_phone_number_to_dograh(
            org_id=101,
            phone_number="+919876543210",
            workflow_id=12
        )
        
        assert mock_request.called
        call_args = mock_request.call_args
        assert "/api/v1/internal/telephony/sync-number" in call_args[0][1]
        assert call_args[1]["json"] == {
            "org_id": 101,
            "phone_number": "+919876543210",
            "workflow_id": 12
        }


@pytest.mark.asyncio
async def test_rest_api_failure_falls_back_to_db_gracefully():
    """Verify that if REST API fails, it falls back to DB if configured."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = {"value": 3}
    mock_session.execute.return_value = mock_result
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None
    
    # Simulate REST API network failure
    with patch("httpx.AsyncClient.request", AsyncMock(side_effect=httpx.ConnectError("Connection refused"))), \
         patch.object(dograh_client, "_has_dograh_db", return_value=True), \
         patch.object(dograh_client, "DograhSessionLocal", return_value=mock_session_ctx):
        
        val = await dograh_client.get_org_config(101, "CONCURRENT_CALL_LIMIT")
        assert val == {"value": 3}
        assert mock_session.execute.called


@pytest.mark.asyncio
async def test_lazy_db_engine_no_eager_connection():
    """Verify that importing or accessing dograh_client does not crash if DB is not reachable."""
    assert hasattr(dograh_client, "DograhSessionLocal")
    # Verify _has_dograh_db is callable
    has_db = dograh_client._has_dograh_db()
    assert isinstance(has_db, bool)
