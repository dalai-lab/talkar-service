"""Comprehensive Unit & Integration Tests for Upstream Vendor Balance & Infrastructure Monitoring."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.vendor_balance_service import VendorBalanceService, VendorCheckResult
from db.models import VendorAlertState, VendorBalanceSnapshot


@pytest.mark.asyncio
async def test_check_plivo_healthy():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "cash_credits": "74.50000",
        "currency": "USD",
        "auto_recharge": True,
    }
    mock_client.get.return_value = mock_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_PLIVO_AUTH_ID = "MAMOCKID"
        mock_settings.TALKAR_PLIVO_AUTH_TOKEN = "secret_token"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_plivo(warning_threshold=20.0, critical_threshold=2.0)

        assert result.vendor == "plivo"
        assert result.status == "healthy"
        assert result.balance == 74.5
        assert result.units == "USD"
        assert result.auto_recharge is True


@pytest.mark.asyncio
async def test_check_plivo_negative_balance_is_critical():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "cash_credits": "-3.25000",
        "currency": "USD",
        "auto_recharge": False,
    }
    mock_client.get.return_value = mock_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_PLIVO_AUTH_ID = "MAMOCKID"
        mock_settings.TALKAR_PLIVO_AUTH_TOKEN = "secret_token"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_plivo(warning_threshold=20.0, critical_threshold=2.0)

        assert result.vendor == "plivo"
        assert result.status == "critical"
        assert result.balance == -3.25


@pytest.mark.asyncio
async def test_check_deepgram_pay_as_you_go_active():
    mock_client = AsyncMock()
    # /balances returns empty array for PAYG accounts
    bal_resp = MagicMock()
    bal_resp.status_code = 200
    bal_resp.json.return_value = {"balances": []}
    mock_client.get.return_value = bal_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_DEEPGRAM_KEY = "dg_secret"
        mock_settings.TALKAR_DEEPGRAM_PROJECT_ID = "proj_123"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_deepgram()

        assert result.vendor == "deepgram"
        assert result.status == "payg_active"
        assert result.units == "payg"


@pytest.mark.asyncio
async def test_check_deepgram_prepaid_balances():
    mock_client = AsyncMock()
    bal_resp = MagicMock()
    bal_resp.status_code = 200
    bal_resp.json.return_value = {
        "balances": [{"balance_id": "b1", "amount": 150.0, "units": "USD"}]
    }
    mock_client.get.return_value = bal_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_DEEPGRAM_KEY = "dg_secret"
        mock_settings.TALKAR_DEEPGRAM_PROJECT_ID = "proj_123"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_deepgram(warning_threshold=20.0, critical_threshold=2.0)

        assert result.vendor == "deepgram"
        assert result.status == "healthy"
        assert result.balance == 150.0


@pytest.mark.asyncio
async def test_check_elevenlabs_overages_enabled_at_zero():
    mock_client = AsyncMock()
    sub_resp = MagicMock()
    sub_resp.status_code = 200
    sub_resp.json.return_value = {
        "character_count": 100000,
        "character_limit": 100000,
        "can_extend_character_limit": True,
        "status": "active",
        "next_character_count_reset_unix": 1735689600,
    }
    mock_client.get.return_value = sub_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_ELEVENLABS_KEY = "el_secret"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_elevenlabs(warning_threshold=50000.0, critical_threshold=5000.0)

        assert result.vendor == "elevenlabs"
        # Overages enabled avoids hard critical
        assert result.status == "warning"
        assert result.overages_enabled is True
        assert result.balance == 0.0


@pytest.mark.asyncio
async def test_check_elevenlabs_past_due_is_critical():
    mock_client = AsyncMock()
    sub_resp = MagicMock()
    sub_resp.status_code = 200
    sub_resp.json.return_value = {
        "character_count": 10000,
        "character_limit": 100000,
        "can_extend_character_limit": False,
        "status": "past_due",
    }
    mock_client.get.return_value = sub_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_ELEVENLABS_KEY = "el_secret"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_elevenlabs()

        assert result.vendor == "elevenlabs"
        assert result.status == "critical"


@pytest.mark.asyncio
async def test_check_smallest_502_returns_error_not_exhausted():
    mock_client = AsyncMock()
    err_resp = MagicMock()
    err_resp.status_code = 502
    err_resp.text = "Bad Gateway"
    mock_client.get.return_value = err_resp

    with patch("services.vendor_balance_service.settings") as mock_settings:
        mock_settings.TALKAR_SMALLEST_AI_KEY = "sm_secret"

        service = VendorBalanceService(http_client=mock_client)
        result = await service.check_smallest()

        assert result.vendor == "smallest"
        assert result.status == "error"
        assert result.balance is None
        assert "502" in (result.error_message or "")


@pytest.mark.asyncio
async def test_hysteresis_and_cooldown_alert_flow():
    """Verify that:
    1. Breaching threshold triggers warning alert.
    2. Rising by < 10% stays in warning (hysteresis).
    3. Rising by > 10% triggers recovery alert.
    """
    now = datetime.now(timezone.utc)
    mock_session = AsyncMock()

    # Initial state: healthy
    state = VendorAlertState(
        vendor="plivo",
        current_tier="healthy",
        warning_threshold=20.0,
        critical_threshold=2.0,
        last_alert_sent_at=None,
        alerts_sent_in_window=0,
        window_start_at=now,
    )
    mock_session.get.return_value = state

    service = VendorBalanceService()

    # Step 1: Drop to $15.00 (below threshold $20.00)
    res_drop = VendorCheckResult(
        vendor="plivo",
        display_name="Plivo Telephony",
        balance=15.0,
        units="USD",
        status="warning",
        warning_threshold=20.0,
        critical_threshold=2.0,
        checked_at=now,
        console_url="",
    )

    with patch("services.vendor_balance_service.send_vendor_balance_alert", AsyncMock(return_value=True)) as mock_alert:
        await service._evaluate_and_dispatch_alert(mock_session, res_drop, now)

        mock_alert.assert_awaited_once()
        assert mock_alert.call_args[1]["tier"] == "warning"
        assert state.current_tier == "warning"

    # Step 2: Tiny fluctuation to $20.50 (above $20.00, but below $22.00 hysteresis buffer)
    res_fluctuate = VendorCheckResult(
        vendor="plivo",
        display_name="Plivo Telephony",
        balance=20.50,
        units="USD",
        status="healthy",
        warning_threshold=20.0,
        critical_threshold=2.0,
        checked_at=now + timedelta(minutes=30),
        console_url="",
    )

    with patch("services.vendor_balance_service.send_vendor_balance_alert", AsyncMock(return_value=True)) as mock_alert_fluc:
        await service._evaluate_and_dispatch_alert(mock_session, res_fluctuate, now + timedelta(minutes=30))
        # Should NOT send recovery yet due to hysteresis buffer ($22.00 required)
        mock_alert_fluc.assert_not_called()
        assert state.current_tier == "warning"

    # Step 3: Top up to $50.00 (comfortably above $22.00)
    res_topup = VendorCheckResult(
        vendor="plivo",
        display_name="Plivo Telephony",
        balance=50.0,
        units="USD",
        status="healthy",
        warning_threshold=20.0,
        critical_threshold=2.0,
        checked_at=now + timedelta(hours=1),
        console_url="",
    )

    with patch("services.vendor_balance_service.send_vendor_balance_alert", AsyncMock(return_value=True)) as mock_alert_rec:
        await service._evaluate_and_dispatch_alert(mock_session, res_topup, now + timedelta(hours=1))
        mock_alert_rec.assert_awaited_once()
        assert mock_alert_rec.call_args[1]["tier"] == "recovered"
        assert state.current_tier == "healthy"


@pytest.mark.asyncio
async def test_vendor_balances_router_get_and_refresh():
    from routers.vendor_balances import get_vendor_balances, refresh_vendor_balances
    mock_admin = MagicMock()
    mock_admin.email = "admin@talkar.in"

    mock_summary = {
        "updated_at": "2026-10-15T18:00:00Z",
        "overall_healthy": True,
        "vendors": {"plivo": {"vendor": "plivo", "status": "healthy"}},
    }

    with patch.object(VendorBalanceService, "get_latest_summary", AsyncMock(return_value=mock_summary)) as mock_get, \
         patch.object(VendorBalanceService, "sync_all_vendors", AsyncMock(return_value=mock_summary)) as mock_sync:
        
        get_res = await get_vendor_balances(admin=mock_admin)
        assert get_res["overall_healthy"] is True
        mock_get.assert_awaited_once()

        ref_res = await refresh_vendor_balances(admin=mock_admin)
        assert ref_res["overall_healthy"] is True
        mock_sync.assert_awaited_once_with(force=True)


@pytest.mark.asyncio
async def test_update_vendor_thresholds_endpoint():
    from routers.vendor_balances import update_vendor_thresholds, ThresholdUpdateRequest
    from fastapi import HTTPException

    mock_admin = MagicMock()
    mock_admin.email = "admin@talkar.in"

    mock_db = AsyncMock()
    existing_state = VendorAlertState(
        vendor="plivo",
        warning_threshold=20.0,
        critical_threshold=2.0,
    )
    mock_db.get.return_value = existing_state

    req = ThresholdUpdateRequest(
        vendor="plivo",
        warning_threshold=35.0,
        critical_threshold=5.0,
    )

    res = await update_vendor_thresholds(request=req, db=mock_db, admin=mock_admin)
    assert res["status"] == "success"
    assert res["warning_threshold"] == 35.0
    assert res["critical_threshold"] == 5.0
    assert existing_state.warning_threshold == 35.0
    mock_db.commit.assert_awaited_once()

    # Invalid vendor should raise 400
    invalid_req = ThresholdUpdateRequest(
        vendor="invalid_vendor",
        warning_threshold=10.0,
        critical_threshold=1.0,
    )
    with pytest.raises(HTTPException) as exc:
        await update_vendor_thresholds(request=invalid_req, db=mock_db, admin=mock_admin)
    assert exc.value.status_code == 400
