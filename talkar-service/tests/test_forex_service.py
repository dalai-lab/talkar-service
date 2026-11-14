import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from db.models import TalkarAdmin
from db.session import get_db
from routers.admin import get_current_admin
from services.forex_service import (
    ForexService,
    DEFAULT_BASELINE_USD_TO_INR,
    MIN_SANITY_RATE,
    MAX_SANITY_RATE,
)


@pytest.mark.asyncio
async def test_forex_service_uninitialized_returns_baseline():
    """ForexService returns baseline immediately if no cache exists."""
    svc = ForexService(baseline_rate=88.0)
    rate = svc.get_usd_to_inr_rate()
    assert rate == 88.0
    meta = svc.get_rate_metadata()
    assert meta["rate"] == 88.0
    assert meta["is_fallback"] is True


@pytest.mark.asyncio
async def test_forex_service_successful_live_refresh():
    """ForexService successfully refreshes and caches rate from primary provider."""
    svc = ForexService(baseline_rate=88.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": "success", "rates": {"INR": 94.35}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        rate = await svc.refresh_rate()

        assert rate == 94.35
        assert svc.get_usd_to_inr_rate() == 94.35
        meta = svc.get_rate_metadata()
        assert meta["is_live"] is True
        assert meta["is_fallback"] is False
        assert meta["provider"] == "open.er-api.com"


@pytest.mark.asyncio
async def test_forex_service_waterfall_failover():
    """ForexService fails over to 2nd provider if 1st fails."""
    svc = ForexService(baseline_rate=88.0)

    # Provider 1 fails with 500
    err_resp = MagicMock()
    err_resp.status_code = 500

    # Provider 2 (frankfurter) succeeds with 94.20
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"base": "USD", "rates": {"INR": 94.20}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [err_resp, ok_resp]
        rate = await svc.refresh_rate()

        assert rate == 94.20
        assert svc.get_rate_metadata()["provider"] == "frankfurter"


@pytest.mark.asyncio
async def test_forex_service_sanity_bounds_guardrail():
    """ForexService rejects out-of-bounds rates (< 70 or > 130) and continues down waterfall."""
    svc = ForexService(baseline_rate=88.0)

    # Provider 1 returns insane rate 0.012 (e.g. inverted currency pair INR/USD)
    insane_resp = MagicMock()
    insane_resp.status_code = 200
    insane_resp.json.return_value = {"rates": {"INR": 0.012}}

    # Provider 2 returns valid rate 94.15
    valid_resp = MagicMock()
    valid_resp.status_code = 200
    valid_resp.json.return_value = {"rates": {"INR": 94.15}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [insane_resp, valid_resp]
        rate = await svc.refresh_rate()

        assert rate == 94.15
        assert svc.get_rate_metadata()["provider"] == "frankfurter"


@pytest.mark.asyncio
async def test_forex_service_all_providers_fail_falls_back():
    """ForexService falls back gracefully to baseline when all APIs error."""
    svc = ForexService(baseline_rate=88.0)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Network down")
        rate = await svc.refresh_rate()

        assert rate == 88.0
        meta = svc.get_rate_metadata()
        assert meta["is_fallback"] is True
        assert meta["provider"] == "baseline_fallback"


def test_admin_forex_endpoints_http():
    """Verify GET /admin/forex-rate and POST /admin/forex-rate/refresh endpoints work via HTTP."""
    client = TestClient(app, raise_server_exceptions=False)
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    app.dependency_overrides[get_current_admin] = lambda: mock_admin

    try:
        # 1. GET /admin/forex-rate
        get_res = client.get("/admin/forex-rate")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert "rate" in get_data
        assert "provider" in get_data
        assert "is_live" in get_data

        # 2. POST /admin/forex-rate/refresh with mock
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"rates": {"INR": 94.40}}

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            post_res = client.post("/admin/forex-rate/refresh")
            assert post_res.status_code == 200
            post_data = post_res.json()
            assert "Successfully refreshed" in post_data["message"]
            assert post_data["metadata"]["rate"] == 94.40
    finally:
        app.dependency_overrides.clear()

