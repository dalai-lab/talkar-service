import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet
from config import resolve_tier_config, TIER_CONFIG
from services.provisioning_service import run_provisioning


def test_resolve_tier_config_handles_custom_plan():
    """Verify Issue 15: resolve_tier_config correctly resolves custom subscription config and rates."""
    custom_sub = Subscription(
        id=1,
        customer_id=10,
        plan="custom",
        per_minute_rate_paise=2500,
        custom_config={
            "concurrent_call_limit": 25,
            "tts_provider": "elevenlabs",
            "per_minute_rate_paise": 2500,
            "max_call_duration_seconds": 1800,
        },
        custom_plan_label="Enterprise VIP"
    )

    cfg = resolve_tier_config(custom_sub)
    assert cfg["concurrent_call_limit"] == 25
    assert cfg["tts_provider"] == "elevenlabs"
    assert cfg["per_minute_rate_paise"] == 2500
    assert cfg["max_call_duration_seconds"] == 1800


def test_resolve_tier_config_handles_string_and_tier_config_custom_key():
    """Verify Issue 15: TIER_CONFIG includes 'custom' and resolve_tier_config accepts 'custom' string."""
    assert "custom" in TIER_CONFIG
    assert TIER_CONFIG["custom"]["concurrent_call_limit"] >= 2
    assert TIER_CONFIG["custom"]["tts_provider"] in ("elevenlabs", "cartesia", "smallest_ai", "deepgram")

    str_cfg = resolve_tier_config("custom")
    assert isinstance(str_cfg, dict)
    assert "concurrent_call_limit" in str_cfg
    assert "tts_provider" in str_cfg


@pytest.mark.asyncio
async def test_sub_org_provisioning_inherits_master_custom_plan():
    """Verify Issue 15: Newly approved sub-org inherits master's custom plan, concurrency 25, ElevenLabs, and ₹25/min."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, status="active", contact_email="agency@corp.com")
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="custom",
        per_minute_rate_paise=2500,
        custom_config={
            "concurrent_call_limit": 25,
            "tts_provider": "elevenlabs",
            "per_minute_rate_paise": 2500,
            "max_call_duration_seconds": 1800,
        },
        custom_plan_label="Agency Enterprise Custom"
    )

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        billing_org_id=1,
        status="active",
        contact_email="client@subcorp.com",
        onboarding_form={}
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    added_objects = []
    def mock_add(obj):
        added_objects.append(obj)
    mock_db.add = MagicMock(side_effect=mock_add)

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master if cid == 1 else sub_org
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                # Master has master_sub (id=1), sub_org (id=2) has None initially
                mock_res.scalar_one_or_none.return_value = master_sub if cid == 1 else None
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        else:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    dograh_upserts = {}
    async def mock_upsert(org_id, key, val):
        dograh_upserts[f"{org_id}:{key}"] = val

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock(side_effect=mock_upsert)), \
         patch("services.provisioning_service.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await run_provisioning(customer_id=2, plan=None, db=mock_db)

    # 1. Verify Dograh concurrency limit was set to 25 (not 2!)
    assert "20:CONCURRENT_CALL_LIMIT" in dograh_upserts
    assert dograh_upserts["20:CONCURRENT_CALL_LIMIT"]["value"] == 25

    # 2. Verify Dograh model configuration was set to ElevenLabs (not Deepgram!)
    assert "20:MODEL_CONFIGURATION_V2" in dograh_upserts
    model_cfg = dograh_upserts["20:MODEL_CONFIGURATION_V2"]
    tts_cfg = model_cfg["byok"]["pipeline"]["tts"]
    assert tts_cfg["provider"] == "elevenlabs"

    # 3. Verify Dograh concurrency group was set to master org (10)
    assert "20:CONCURRENCY_GROUP_ID" in dograh_upserts
    assert dograh_upserts["20:CONCURRENCY_GROUP_ID"]["value"] == 10

    # 4. Verify sub-org Subscription was created with master's custom rate and config
    created_subs = [obj for obj in added_objects if isinstance(obj, Subscription)]
    assert len(created_subs) == 1
    sub_record = created_subs[0]
    assert sub_record.customer_id == 2
    assert sub_record.plan == "custom"
    assert sub_record.per_minute_rate_paise == 2500  # ₹25/min (not 600!)
    assert sub_record.custom_config == master_sub.custom_config
    assert sub_record.custom_plan_label == "Agency Enterprise Custom"


@pytest.mark.asyncio
async def test_sub_org_reprovisioning_updates_when_master_custom_plan_changes():
    """Verify Issue 15: Re-provisioning an existing sub-org syncs updated master custom rate & config."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, status="active", contact_email="agency@corp.com")
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="custom",
        per_minute_rate_paise=3000,  # Updated to ₹30/min
        custom_config={
            "concurrent_call_limit": 30,  # Updated to 30 channels
            "tts_provider": "elevenlabs",
            "per_minute_rate_paise": 3000,
        },
        custom_plan_label="Updated Tier 30"
    )

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        billing_org_id=1,
        status="active",
        contact_email="client@subcorp.com"
    )

    existing_sub = Subscription(
        id=2,
        customer_id=2,
        plan="custom",
        per_minute_rate_paise=2000,
        custom_config={"concurrent_call_limit": 20, "tts_provider": "elevenlabs"}
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute_sub(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master if cid == 1 else sub_org
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master_sub if cid == 1 else existing_sub
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        else:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute_sub)

    dograh_upserts = {}
    async def mock_upsert(org_id, key, val):
        dograh_upserts[f"{org_id}:{key}"] = val

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock(side_effect=mock_upsert)), \
         patch("services.provisioning_service.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await run_provisioning(customer_id=2, plan=None, db=mock_db)

    # Concurrency in Dograh updated to 30
    assert dograh_upserts["20:CONCURRENT_CALL_LIMIT"]["value"] == 30

    # Subscription row updated to 3000 paise & new custom_config
    assert existing_sub.per_minute_rate_paise == 3000
    assert existing_sub.custom_config == master_sub.custom_config
    assert existing_sub.custom_plan_label == "Updated Tier 30"
