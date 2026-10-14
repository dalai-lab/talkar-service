import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription
from routers.customers import update_customer_voice, UpdateVoiceRequest
from config import settings


@pytest.mark.asyncio
async def test_suborg_under_pro_master_can_configure_elevenlabs():
    """
    Issue 23:
    Sub-org under a Pro master account must be able to select ElevenLabs voices
    without being locked out with HTTP 403.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=2,
        dograh_org_id=202,
        status="active",
        billing_org_id=1,
    )
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="pro",
        per_minute_rate_paise=1800,
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    existing_dograh_cfg = {
        "byok": {
            "pipeline": {
                "tts": {
                    "provider": "deepgram",
                    "voice": "aura-2-thalia-en",
                    "api_key": "old_dg_key",
                }
            }
        }
    }

    with (
        patch("services.dograh_client.get_org_config", AsyncMock(return_value=existing_dograh_cfg)),
        patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert,
        patch.object(settings, "TALKAR_ELEVENLABS_KEY", "eleven_key_123"),
    ):
        res = await update_customer_voice(
            org_id=202,
            data=UpdateVoiceRequest(voice_id="21m00Tcm4TlvDq8ikWAM", provider="elevenlabs"),
            db=mock_db,
        )

    assert res["status"] == "success"
    assert res["provider"] == "elevenlabs"
    assert res["voice_id"] == "21m00Tcm4TlvDq8ikWAM"

    mock_upsert.assert_awaited_once()
    upserted_cfg = mock_upsert.await_args[0][2]
    tts = upserted_cfg["byok"]["pipeline"]["tts"]
    assert tts["provider"] == "elevenlabs"
    assert tts["voice"] == "21m00Tcm4TlvDq8ikWAM"
    assert tts["api_key"] == "eleven_key_123"
    assert tts["model"] == "eleven_flash_v2_5"


@pytest.mark.asyncio
async def test_custom_plan_client_can_configure_contracted_voice():
    """
    Issue 23:
    Enterprise clients on a Custom Plan (plan="custom") with contracted voice provider
    (e.g., ElevenLabs) in custom_config must not be locked out with HTTP 403.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    customer = Customer(
        id=10,
        dograh_org_id=1001,
        status="active",
        billing_org_id=None,
    )
    custom_sub = Subscription(
        id=10,
        customer_id=10,
        plan="custom",
        per_minute_rate_paise=2000,
        custom_config={
            "concurrent_call_limit": 25,
            "tts_provider": "elevenlabs",
        },
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = custom_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    existing_dograh_cfg = {
        "byok": {
            "pipeline": {
                "tts": {
                    "provider": "elevenlabs",
                    "voice": "old_voice",
                    "api_key": "some_key",
                }
            }
        }
    }

    with (
        patch("services.dograh_client.get_org_config", AsyncMock(return_value=existing_dograh_cfg)),
        patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert,
        patch.object(settings, "TALKAR_ELEVENLABS_KEY", "eleven_key_123"),
    ):
        res = await update_customer_voice(
            org_id=1001,
            data=UpdateVoiceRequest(voice_id="JBFqnCBsd6RMkjVDRZzb", provider="elevenlabs"),
            db=mock_db,
        )

    assert res["status"] == "success"
    assert res["provider"] == "elevenlabs"
    assert res["voice_id"] == "JBFqnCBsd6RMkjVDRZzb"


@pytest.mark.asyncio
async def test_suborg_on_custom_plan_inherits_contracted_voice():
    """
    Issue 23:
    Sub-org under an enterprise Custom Plan master inherits the master's custom voice
    configuration and can configure that contracted voice.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=20,
        dograh_org_id=2001,
        status="active",
        billing_org_id=10,
    )
    master_sub = Subscription(
        id=10,
        customer_id=10,
        plan="custom",
        per_minute_rate_paise=2500,
        custom_config={
            "concurrent_call_limit": 50,
            "tts_provider": "elevenlabs",
        },
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    existing_dograh_cfg = {
        "byok": {
            "pipeline": {
                "tts": {
                    "provider": "elevenlabs",
                    "voice": "old_voice",
                    "api_key": "some_key",
                }
            }
        }
    }

    with (
        patch("services.dograh_client.get_org_config", AsyncMock(return_value=existing_dograh_cfg)),
        patch("services.dograh_client.upsert_org_config", AsyncMock()),
        patch.object(settings, "TALKAR_ELEVENLABS_KEY", "eleven_key_123"),
    ):
        res = await update_customer_voice(
            org_id=2001,
            data=UpdateVoiceRequest(voice_id="pNInz6obpgDQGcFmaJgB", provider="elevenlabs"),
            db=mock_db,
        )

    assert res["status"] == "success"
    assert res["provider"] == "elevenlabs"


@pytest.mark.asyncio
async def test_suborg_approved_for_growth_can_configure_smallest_ai():
    """
    Issue 23:
    Sub-org with approved_tier='growth' in onboarding_form can configure Smallest AI voices.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=30,
        dograh_org_id=3001,
        status="active",
        billing_org_id=1,
        onboarding_form={"approved_tier": "growth"},
    )
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="starter",
        per_minute_rate_paise=600,
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    existing_dograh_cfg = {
        "byok": {
            "pipeline": {
                "tts": {
                    "provider": "deepgram",
                    "voice": "aura-2-thalia-en",
                    "api_key": "dg_key",
                }
            }
        }
    }

    with (
        patch("services.dograh_client.get_org_config", AsyncMock(return_value=existing_dograh_cfg)),
        patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert,
        patch.object(settings, "TALKAR_SMALLEST_AI_KEY", "smallest_key_123"),
    ):
        res = await update_customer_voice(
            org_id=3001,
            data=UpdateVoiceRequest(voice_id="mishka", provider="smallest_ai"),
            db=mock_db,
        )

    assert res["status"] == "success"
    assert res["provider"] == "smallest"
    assert res["voice_id"] == "mishka"

    upserted_cfg = mock_upsert.await_args[0][2]
    tts = upserted_cfg["byok"]["pipeline"]["tts"]
    assert tts["provider"] == "smallest"
    assert tts["api_key"] == "smallest_key_123"
    assert tts["model"] == "lightning_v3.1_pro"


@pytest.mark.asyncio
async def test_suborg_approved_for_growth_rejects_elevenlabs():
    """
    Issue 23:
    Sub-org with approved_tier='growth' rejects ElevenLabs voice selection with 403.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=30,
        dograh_org_id=3001,
        status="active",
        billing_org_id=1,
        onboarding_form={"approved_tier": "growth"},
    )
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="pro",
        per_minute_rate_paise=1800,
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    with pytest.raises(HTTPException) as exc_info:
        await update_customer_voice(
            org_id=3001,
            data=UpdateVoiceRequest(voice_id="21m00Tcm4TlvDq8ikWAM", provider="elevenlabs"),
            db=mock_db,
        )

    assert exc_info.value.status_code == 403
    assert "only supports smallest_ai voices" in exc_info.value.detail
