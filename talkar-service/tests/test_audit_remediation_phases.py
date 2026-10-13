import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services import razorpay_client, notification_service
from config import settings

@pytest.mark.asyncio
async def test_cron_timezone_import():
    """Verify that jobs/cron.py correctly imports timezone and dispatch_scheduled_reports constructs UTC time without NameError."""
    import jobs.cron as cron_module
    from datetime import timezone
    assert hasattr(cron_module, "timezone")
    assert cron_module.timezone is timezone

def test_webhook_fail_closed_in_production():
    """Verify that verify_webhook_signature fails closed (returns False) in production when secret is unset."""
    with patch.object(settings, "RAZORPAY_WEBHOOK_SECRET", ""):
        with patch.object(settings, "ENVIRONMENT", "production"):
            result = razorpay_client.verify_webhook_signature(b"{}", "dummy_sig")
            assert result is False, "Webhook signature verification must FAIL CLOSED in production"

        with patch.object(settings, "ENVIRONMENT", "development"):
            result = razorpay_client.verify_webhook_signature(b"{}", "dummy_sig")
            assert result is True, "Webhook signature verification should return True (mock mode) in dev"

@pytest.mark.asyncio
async def test_service_paused_24h_cooldown():
    """Verify that notify_customer_service_paused enforces a 24h Redis cooldown key to prevent duplicate alert emails."""
    mock_redis = AsyncMock()
    # First call: set returns True (key was set, not in cooldown)
    mock_redis.set.return_value = True

    with patch("services.redis_client.get_redis", return_value=mock_redis), \
         patch("services.notification_service._get_customer_email", return_value=("test@example.com", "Acme")), \
         patch("services.notification_service.send_email_and_push", new_callable=AsyncMock) as mock_send:
        
        await notification_service.notify_customer_service_paused(customer_id=42)
        assert mock_send.call_count == 1
        mock_redis.set.assert_called_once_with("alert_paused_cooldown:42", "1", nx=True, ex=86400)

    # Second call: set returns None/False (key exists, in cooldown)
    mock_redis.set.return_value = None
    with patch("services.redis_client.get_redis", return_value=mock_redis), \
         patch("services.notification_service._get_customer_email", return_value=("test@example.com", "Acme")), \
         patch("services.notification_service.send_email_and_push", new_callable=AsyncMock) as mock_send:
        
        await notification_service.notify_customer_service_paused(customer_id=42)
        assert mock_send.call_count == 0, "Duplicate email should have been suppressed by 24h cooldown"

@pytest.mark.asyncio
async def test_admin_billing_dlq_endpoints():
    """Verify get_billing_dlq and replay_billing_dlq endpoints."""
    from routers.admin import get_billing_dlq, replay_billing_dlq
    import json

    mock_redis = AsyncMock()
    mock_redis.lrange.return_value = [
        json.dumps({
            "workflow_run_id": 999,
            "payload": {
                "workflow_run_id": 999,
                "duration_seconds": 65,
                "dograh_org_id": 12,
            },
            "error": "Timeout"
        })
    ]
    mock_redis.llen.return_value = 1

    mock_admin = MagicMock()
    mock_admin.email = "admin@talkar.in"

    with patch("services.redis_client.get_redis", return_value=mock_redis):
        res = await get_billing_dlq(current_admin=mock_admin)
        assert res["count"] == 1
        assert len(res["items"]) == 1
        assert res["items"][0]["workflow_run_id"] == 999

    # Test replay
    mock_redis.lpop.side_effect = [
        json.dumps({
            "workflow_run_id": 999,
            "payload": {
                "workflow_run_id": 999,
                "duration_seconds": 65,
                "dograh_org_id": 12,
            }
        }),
        None
    ]
    mock_redis.llen.return_value = 0
    mock_db = AsyncMock()

    with patch("services.redis_client.get_redis", return_value=mock_redis), \
         patch("routers.billing.deduct_for_run", new_callable=AsyncMock) as mock_deduct:
        replay_res = await replay_billing_dlq(current_admin=mock_admin, db=mock_db)
        assert replay_res["replayed"] == 1
        assert replay_res["failed"] == 0
        assert mock_deduct.call_count == 1
