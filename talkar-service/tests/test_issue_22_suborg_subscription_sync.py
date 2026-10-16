import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, TalkarAdmin
from routers.billing import check_quota, deduct_for_run, DograhQuotaRequest, DograhDeductRequest
from services.provisioning_service import run_provisioning
from routers.admin import update_customer, CustomerUpdateRequest
from config import TIER_CONFIG


@pytest.mark.asyncio
async def test_sub_org_provisioning_inherits_master_subscription_rate_and_config():
    """Verify Issue 22 / Fix 24: Sub-org inherits master plan, rate, and custom config during provisioning."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, status="active", contact_email="master@test.com")
    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="growth",
        per_minute_rate_paise=900,
        custom_config={"tts_provider": "cartesia", "concurrent_call_limit": 5},
        custom_plan_label="Growth Plus",
        status="active"
    )

    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        billing_org_id=1,
        status="active",
        contact_email="sub@test.com",
        onboarding_form={}
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    added_objects = []
    mock_db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

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
                mock_res.scalar_one_or_none.return_value = master_sub if cid == 1 else None
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        else:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock()), \
         patch("services.provisioning_service.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await run_provisioning(customer_id=2, plan=None, db=mock_db)

    # Verify that a subscription for sub_org was added with master's plan, rate, and custom config
    created_subs = [obj for obj in added_objects if isinstance(obj, Subscription)]
    assert len(created_subs) == 1
    sub_record = created_subs[0]
    assert sub_record.customer_id == 2
    assert sub_record.plan == "growth"
    assert sub_record.per_minute_rate_paise == 900
    assert sub_record.custom_config == master_sub.custom_config
    assert sub_record.custom_plan_label == "Growth Plus"


@pytest.mark.asyncio
async def test_admin_tier_upgrade_cascades_subscription_to_sub_orgs():
    """Verify Issue 22 / Fix 24: Admin updating master tier cascades plan and rate to sub-org subscriptions."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, status="active", onboarding_form={})
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, status="active", onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600, status="active")
    sub_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600, status="active")

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    subs = {1: master_sub, 2: sub_sub}

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = subs.get(cid, master_sub)
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = CustomerUpdateRequest(tier="pro")

    with patch("services.provisioning_service.run_provisioning", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        res = await update_customer(customer_id=1, data=req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    # Sub-org subscription must be updated to Pro tier with Pro rates
    assert sub_sub.plan == "pro"
    assert sub_sub.per_minute_rate_paise == TIER_CONFIG["pro"]["per_minute_rate_paise"]


@pytest.mark.asyncio
async def test_sub_org_check_quota_and_deduct_resolve_master_custom_rate():
    """Verify Issue 22 / Fix 24: check_quota and /deduct resolve rates from master active custom subscription."""
    master = Customer(id=1, dograh_org_id=10, billing_org_id=None, status="active")
    sub_org = Customer(id=2, dograh_org_id=20, billing_org_id=1, status="active", onboarding_form={})

    master_sub = Subscription(
        id=1,
        customer_id=1,
        plan="custom",
        per_minute_rate_paise=2500,  # ₹25/min
        custom_config={"tts_provider": "elevenlabs", "concurrent_call_limit": 20, "max_call_duration_seconds": 1800},
        status="active"
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1000000)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    added_txns = []
    mock_db.add = MagicMock(side_effect=lambda obj: added_txns.append(obj))

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "call_logs.dograh_run_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "agents" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "RETURNING" in q_str:
            master_wallet.balance_paise = 950000
            mock_res.scalar_one_or_none.return_value = master_wallet
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    # 1. check_quota
    quota_req = DograhQuotaRequest(
        organization_id=20,
        mode="telephony",
        active_calls=1
    )
    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))):
        quota_res = await check_quota(quota_req, db=mock_db, _token="valid")

    assert quota_res["has_quota"] is True

    # 2. deduct_for_run (duration 120s = 2 minutes * 2500 paise/min = 5000 paise)
    deduct_req = DograhDeductRequest(
        organization_id=20,
        workflow_run_id=50001,
        duration_seconds=120
    )
    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(master_wallet, 1))), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        deduct_res = await deduct_for_run(deduct_req, db=mock_db, _token="valid")

    assert deduct_res["status"] == "ok"
    assert deduct_res["cost_paise"] == 5000  # 2 min * 2500 paise/min
