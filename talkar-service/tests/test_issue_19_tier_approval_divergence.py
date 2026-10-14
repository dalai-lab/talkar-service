import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, TalkarAdmin
from routers.admin import approve_application, ApproveApplicationRequest
from routers.billing import check_quota, deduct_for_run, DograhQuotaRequest, DograhDeductRequest
from services.billing_service import deduct_for_run as billing_service_deduct
from services.provisioning_service import run_provisioning


@pytest.mark.asyncio
async def test_admin_approve_application_persists_approved_tier_and_sub():
    """
    Issue 19: When an admin approves an application with approved_tier="starter",
    the approved_tier must be persisted into onboarding_form, and Subscription must be
    created/updated with the approved starter rate (600 paise/min).
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="pending_approval",
        onboarding_form={},
        contact_email="branch@test.com",
        contact_name="Branch Manager"
    )
    admin = TalkarAdmin(id=1, email="admin@talkar.in")

    db = AsyncMock()
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [cust_res, sub_res]

    req = ApproveApplicationRequest(
        integration_fee_paise=0,
        integration_description="Standard setup",
        approved_tier="starter"
    )

    with patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.notification_service.send_email", AsyncMock()):
        res = await approve_application(customer_id=20, data=req, db=db, current_admin=admin)

    assert res["status"] == "agent_building"
    assert sub_org.onboarding_form["approved_tier"] == "starter"
    assert sub_org.status == "agent_building"
    mock_prov.assert_awaited_once_with(20, "starter", db)


@pytest.mark.asyncio
async def test_sub_org_on_starter_bills_at_starter_rate_under_pro_master():
    """
    Issue 19: When a sub-org is on Starter (₹6.00/min, 2 calls) under a Pro master (₹18.00/min),
    deduct_for_run must bill the sub-org at Starter rate (600 paise/min), NOT the Master's Pro rate.
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="active",
        onboarding_form={"approved_tier": "starter"}
    )
    master = Customer(id=1, billing_org_id=None, status="active")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    db = AsyncMock()

    # Query 1: select Customer by dograh_org_id
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: CallLog idempotency check
    cl_res = MagicMock()
    cl_res.scalar_one_or_none.return_value = None

    # Query 3: master Subscription query (sub_customer_id = 1)
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = master_sub

    # Query 4a: get_billing_wallet customer(20)
    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org

    # Query 4b: get_billing_wallet master customer(1)
    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master

    # Query 4c: get_billing_wallet wallet(1)
    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    # Query 5: update(Wallet)
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=498800)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 6: refreshed wallet after recharge check
    refreshed_wallet_res = MagicMock()
    refreshed_wallet_res.scalar_one_or_none.return_value = updated_wallet

    db.execute.side_effect = [
        cust_res,
        cl_res,
        sub_res,
        bw_c1,
        bw_c2,
        bw_w,
        update_res,
        refreshed_wallet_res,
    ]

    deduct_req = DograhDeductRequest(
        organization_id=102,
        duration_seconds=120,  # 2 minutes
        workflow_run_id=88881
    )

    with patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.dograh_client.get_run", AsyncMock(return_value=None)):
        res = await deduct_for_run(deduct_req, db=db)

    assert res["status"] == "ok"
    # 2 minutes * 600 paise/min = 1200 paise (Starter rate), NOT 3600 paise (Master Pro rate)
    assert res["cost_paise"] == 1200

    added_objects = [call[0][0] for call in db.add.call_args_list]
    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    assert call_logs[0].cost_to_customer_paise == 1200
    assert call_logs[0].plan == "starter"
    assert call_logs[0].tts_provider == "deepgram"


@pytest.mark.asyncio
async def test_sub_org_on_starter_check_quota_scales_starter_reserve_under_pro_master():
    """
    Issue 19: check_quota calculates reserve requirement and capacity based on Starter tier
    (2 calls * 15 min * 600 paise = 18,000 paise reserve), NOT Master Pro tier (54,000 paise).
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="active",
        onboarding_form={"approved_tier": "starter"}
    )
    master = Customer(id=1, dograh_org_id=101, status="active")
    # Master wallet has 52,000 paise (above CALL_BLOCK_THRESHOLD_PAISE of 50,000 paise).
    # Under Starter rate (600 paise): 2 calls * 15 min * 600 paise = 18,000 paise required -> GRANTED (52k >= 18k).
    # Under Pro rate (1800 paise): 2 calls * 15 min * 1800 paise = 54,000 paise required -> WOULD FAIL (52k < 54k).
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=52000)
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    db = AsyncMock()

    # Query 1: select Customer by dograh_org_id
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: master check in check_quota
    master_res = MagicMock()
    master_res.scalar_one_or_none.return_value = master

    # Query 3: master Subscription query
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = master_sub

    db.execute.side_effect = [cust_res, master_res, sub_res]

    req = DograhQuotaRequest(
        organization_id=102,
        mode="telephony",
        active_calls=1
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))):
        res = await check_quota(data=req, db=db, _token="test_token")

    # With Starter rate (600 paise/min): 1 call * 30 min * 600 paise = 18,000 paise required.
    # Wallet balance is 52,000 paise >= 18,000 paise, so quota is GRANTED.
    # If Pro rate (1800 paise/min) was used, 54,000 paise would be required and falsely rejected (52k < 54k).
    assert res["has_quota"] is True


@pytest.mark.asyncio
async def test_billing_service_deduct_reconciliation_honors_sub_org_starter_tier():
    """
    Issue 19: Nightly cron / background reconciliation in billing_service.deduct_for_run
    bills sub-org at Starter rate (600 paise/min) when onboarding_form specifies approved_tier="starter".
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="active",
        onboarding_form={"approved_tier": "starter"}
    )
    master = Customer(id=1, billing_org_id=None, status="active")
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    db = AsyncMock()

    # Query 1: select Customer by dograh_org_id
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select Subscription for customer(20) -> None
    sub_sub_res = MagicMock()
    sub_sub_res.scalar_one_or_none.return_value = None

    # Query 2b: select master Subscription -> master_sub
    master_sub_res = MagicMock()
    master_sub_res.scalar_one_or_none.return_value = master_sub

    # Query 3: CallLog idempotency check
    cl_res = MagicMock()
    cl_res.scalar_one_or_none.return_value = None

    # Query 4: get_billing_wallet c(20)
    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org

    # Query 5: get_billing_wallet master(1)
    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master

    # Query 6: get_billing_wallet wallet(1)
    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    # Query 7: update(Wallet)
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=498800)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 8: re-fetch wallet after auto-recharge
    refreshed_res = MagicMock()
    refreshed_res.scalar_one_or_none.return_value = updated_wallet

    db.execute.side_effect = [
        cust_res,
        sub_sub_res,
        master_sub_res,
        cl_res,
        bw_c1,
        bw_c2,
        bw_w,
        update_res,
        refreshed_res,
    ]

    run_data = {
        "organization_id": 102,
        "duration_seconds": 120,
        "mode": "telephony"
    }

    added_objects = []
    db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

    with patch("services.dograh_client.get_run", AsyncMock(return_value=run_data)), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        await billing_service_deduct(db=db, run_id=99991)

    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    # 2 min * 600 paise/min = 1200 paise, NOT 3600 paise
    assert call_logs[0].cost_to_customer_paise == 1200
    assert call_logs[0].plan == "starter"


@pytest.mark.asyncio
async def test_run_provisioning_configures_starter_limits_for_sub_org_under_pro_master():
    """
    Issue 19: run_provisioning sets CONCURRENT_CALL_LIMIT=2 and TTS=deepgram for a sub-org
    approved on Starter even when the master account is on Pro.
    """
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="agent_building",
        onboarding_form={"approved_tier": "starter"}
    )
    master = Customer(id=1, dograh_org_id=101, status="active")
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    db = AsyncMock()

    # Query 1: select Customer(20)
    c_res = MagicMock()
    c_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select master_sub(1)
    ms_res = MagicMock()
    ms_res.scalar_one_or_none.return_value = master_sub

    # Query 3: select Subscription(20)
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = None

    # Query 4: master_cust lookup for CONCURRENCY_GROUP_ID
    mc_res = MagicMock()
    mc_res.scalar_one_or_none.return_value = master

    # Query 5: existing_sub_check
    esc_res = MagicMock()
    esc_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [
        c_res,
        ms_res,
        sub_res,
        mc_res,
        esc_res,
    ]

    with patch("services.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert, \
         patch("services.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await run_provisioning(customer_id=20, plan="starter", db=db)

    # Check upsert calls
    upsert_calls = {call[0][1]: call[0][2] for call in mock_upsert.call_args_list}

    # Verify Dograh received Starter limit (2 calls), NOT Pro limit (10 calls)
    assert upsert_calls["CONCURRENT_CALL_LIMIT"] == {"value": 2, "is_custom_cap": False}

    # Verify Dograh received Deepgram TTS (Starter TTS), NOT ElevenLabs (Pro TTS)
    model_cfg = upsert_calls["MODEL_CONFIGURATION_V2"]
    tts_cfg = model_cfg["byok"]["pipeline"]["tts"]
    assert tts_cfg["provider"] == "deepgram"
