import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, Agent
from routers.customers import (
    SelectTierRequest,
    TierUpgradeRequest,
    UpdateVoiceRequest,
    select_tier,
    request_tier_upgrade,
    update_customer_voice,
)
from routers.billing import DograhDeductRequest, DograhQuotaRequest, deduct_for_run, check_quota
from config import TIER_CONFIG


@pytest.mark.asyncio
async def test_sub_org_select_tier_rejects_tier_higher_than_master():
    """A sub-organization cannot self-select a tier higher than master organization plan."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="client@suborg.com",
        billing_org_id=1,
        status="pending_plan_selection",
    )
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_master_sub = MagicMock()
    res_master_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_master_sub])

    with pytest.raises(HTTPException) as exc_info:
        await select_tier(org_id=20, data=SelectTierRequest(tier="pro"), db=mock_db)

    assert exc_info.value.status_code == 400
    assert "cannot exceed master organization plan" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sub_org_select_tier_permitted_when_within_master_tier():
    """A sub-organization is permitted to select a tier equal to or below master's plan."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        contact_email="client@suborg.com",
        billing_org_id=1,
        status="pending_plan_selection",
        onboarding_form={},
    )
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_master_sub = MagicMock()
    res_master_sub.scalar_one_or_none.return_value = master_sub
    res_sub_sub = MagicMock()
    res_sub_sub.scalar_one_or_none.return_value = None

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_master_sub, res_sub_sub])

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))),
        patch("services.provisioning_service.run_provisioning", AsyncMock()),
        patch("services.notification_service.send_email", AsyncMock()),
    ):
        result = await select_tier(org_id=20, data=SelectTierRequest(tier="pro"), db=mock_db)
        assert result["status"] == "active"
        assert sub_org.onboarding_form["approved_tier"] == "pro"


@pytest.mark.asyncio
async def test_sub_org_update_voice_rejects_elevenlabs_when_master_is_starter():
    """Sub-org cannot configure ElevenLabs voices if the master account is on Starter tier."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
    )
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_sub])

    with pytest.raises(HTTPException) as exc_info:
        await update_customer_voice(
            org_id=20,
            data=UpdateVoiceRequest(voice_id="21m00Tcm4TlvDq8ikWAM", provider="elevenlabs"),
            db=mock_db,
        )

    assert exc_info.value.status_code == 403
    assert "only supports deepgram voices" in exc_info.value.detail


@pytest.mark.asyncio
async def test_deduct_for_run_uprates_elevenlabs_to_protect_gross_margin():
    """
    If a call runs with ElevenLabs (e.g. sub-org on pro or custom voice) while master is
    billed at Starter rate (600 paise), deduct_for_run up-rates the per-minute fee to 1800 paise
    (and at least 1200 floor) to prevent Talkar from losing money per minute.
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    # Sub-org customer with approved_tier="pro" (ElevenLabs)
    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"approved_tier": "pro"},
    )
    # Master is on Starter (600 paise/min)
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    master_wallet_post = Wallet(id=1, customer_id=1, balance_paise=496400)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub
    res_deduct = MagicMock()
    res_deduct.scalar_one_or_none.return_value = master_wallet_post
    res_refresh = MagicMock()
    res_refresh.scalar_one_or_none.return_value = master_wallet_post

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub, res_deduct, res_refresh])
    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

    req = DograhDeductRequest(
        workflow_run_id=505,
        duration_seconds=120,  # 2 minutes
        organization_id=20,
        mode="telephony",
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))),
        patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()),
    ):
        result = await deduct_for_run(data=req, db=mock_db, _token="test_token")
        assert result["status"] == "ok"

        call_logs = [obj for obj in added_objects if isinstance(obj, CallLog)]
        assert len(call_logs) == 1
        log = call_logs[0]

        # 2 minutes * 1800 paise/min (pro rate) = 3600 paise (instead of 1200 paise at starter rate)
        assert log.cost_to_customer_paise == 3600
        assert log.plan == "pro"
        assert log.tts_provider == "elevenlabs"


@pytest.mark.asyncio
async def test_check_quota_scales_reserve_for_elevenlabs_sub_org():
    """
    check_quota must calculate reserve requirements based on the premium ElevenLabs rate (1800 paise)
    rather than Starter rate (600 paise) when sub-org uses ElevenLabs.
    """
    mock_db = MagicMock()

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"approved_tier": "pro"},
    )
    master_cust = Customer(id=1, dograh_org_id=10, status="active")
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)

    # Master wallet has 52,000 paise (above CALL_BLOCK_THRESHOLD_PAISE of 50,000):
    # Under starter rate: 2 calls * 15 min * 600 paise = 18,000 paise required -> would pass (52k >= 18k).
    # Under pro rate: 2 calls * 15 min * 1800 paise = 54,000 paise required -> MUST fail (52k < 54k).
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=52000)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_org
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master_cust
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = master_sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_master, res_sub])

    req = DograhQuotaRequest(
        organization_id=20,
        mode="telephony",
        active_calls=2,
    )

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))),
        patch("services.billing_service.trigger_auto_recharge_background", AsyncMock()),
    ):
        result = await check_quota(data=req, db=mock_db, _token="test_token")
        assert result["has_quota"] is False
        assert result["reason"] == "insufficient_reserve"
        assert result["required_paise"] == 54000  # 2 calls * 15 min * 1800 paise
        assert result["balance_paise"] == 52000


@pytest.mark.asyncio
async def test_request_tier_upgrade_from_sub_org_rejected_and_master_cascades():
    """Calling request_tier_upgrade on a sub-org is rejected (403), while master upgrade cascades down to all sub-orgs."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    master = Customer(id=1, dograh_org_id=10, status="active", onboarding_form={})
    sub_org_1 = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    sub_org_2 = Customer(id=3, dograh_org_id=30, status="active", billing_org_id=1, onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub_1 = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    sub_sub_2 = Subscription(id=3, customer_id=3, plan="starter", per_minute_rate_paise=600)

    # 1. Sub-org attempt must be rejected with 403 (Blocker 23)
    res_sub_cust = MagicMock()
    res_sub_cust.scalar_one_or_none.return_value = sub_org_1
    mock_db.execute = AsyncMock(return_value=res_sub_cust)

    with pytest.raises(HTTPException) as exc_info:
        await request_tier_upgrade(org_id=20, data=TierUpgradeRequest(requested_tier="growth"), db=mock_db)
    assert exc_info.value.status_code == 403
    assert "Sub-organizations cannot change agency-wide billing plans" in exc_info.value.detail

    # 2. Master org upgrade succeeds and cascades to sub-orgs
    res_master_cust = MagicMock()
    res_master_cust.scalar_one_or_none.return_value = master
    res_master_sub = MagicMock()
    res_master_sub.scalar_one_or_none.return_value = master_sub
    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = Wallet(id=1, customer_id=1, balance_paise=1000000)
    res_suborgs = MagicMock()
    res_suborgs.scalars.return_value.all.return_value = [sub_org_1, sub_org_2]
    res_sub_sub_1 = MagicMock()
    res_sub_sub_1.scalar_one_or_none.return_value = sub_sub_1
    res_sub_sub_2 = MagicMock()
    res_sub_sub_2.scalar_one_or_none.return_value = sub_sub_2

    mock_db.execute = AsyncMock(side_effect=[
        res_master_cust,
        res_master_sub,
        res_wallet,
        res_suborgs,
        res_sub_sub_1,
        res_sub_sub_2,
    ])

    with (
        patch("services.provisioning_service.run_provisioning", AsyncMock()),
        patch("services.notification_service.send_email", AsyncMock()),
    ):
        result = await request_tier_upgrade(org_id=10, data=TierUpgradeRequest(requested_tier="growth"), db=mock_db)
        assert result["status"] == "success"
        assert master_sub.plan == "growth"
        assert sub_sub_1.plan == "growth"
        assert sub_sub_2.plan == "growth"

