import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet
from routers.billing import confirm_topup, ConfirmTopupRequest, razorpay_webhook
from routers.customers import request_tier_upgrade, TierUpgradeRequest
from services import provisioning_service, dograh_client, billing_service


@pytest.mark.asyncio
async def test_suborg_under_review_and_agent_building_reprovisioned_on_confirm_topup_upgrade():
    """
    Verify Issue 10:
    When Master upgrades from Starter (2 calls) to Pro (10 calls) via confirm_topup,
    sub-orgs in 'under_review' and 'agent_building' MUST be reprovisioned alongside active ones,
    preventing concurrency freeze.
    """
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_review = Customer(id=2, dograh_org_id=200, status="under_review", billing_org_id=1, onboarding_form={})
    sub_building = Customer(id=3, dograh_org_id=300, status="agent_building", billing_org_id=1, onboarding_form={})
    sub_active = Customer(id=4, dograh_org_id=400, status="active", billing_org_id=1, onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub2_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)
    sub3_sub = Subscription(id=3, customer_id=3, plan="starter", per_minute_rate_paise=600)
    sub4_sub = Subscription(id=4, customer_id=4, plan="starter", per_minute_rate_paise=600)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=1500000)

    subs = {1: master_sub, 2: sub2_sub, 3: sub3_sub, 4: sub4_sub}

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_building
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = subs.get(cid, master_sub)
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_review, sub_building, sub_active]
        elif "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = ConfirmTopupRequest(
        dograh_org_id=300,
        razorpay_order_id="order_pro_123",
        razorpay_payment_id="pay_pro_123",
        razorpay_signature="mock_sig",
        amount_paise=800000,
        requested_tier="pro"
    )

    with patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        res = await confirm_topup(req, db=mock_db)

    assert res["status"] == "ok"
    assert master_sub.plan == "pro"
    assert sub2_sub.plan == "pro"
    assert sub3_sub.plan == "pro"
    assert sub4_sub.plan == "pro"

    # All sub-orgs must have approved_tier set to pro
    assert sub_review.onboarding_form["approved_tier"] == "pro"
    assert sub_building.onboarding_form["approved_tier"] == "pro"
    assert sub_active.onboarding_form["approved_tier"] == "pro"

    # Crucial Issue 10 check: run_provisioning MUST be called for master AND all sub-orgs
    prov_cids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_cids, "Master must be reprovisioned"
    assert 2 in prov_cids, "Sub-org in under_review must be reprovisioned"
    assert 3 in prov_cids, "Sub-org in agent_building must be reprovisioned"
    assert 4 in prov_cids, "Sub-org in active must be reprovisioned"

    # sync_wallet_block_policy must be called
    mock_sync.assert_awaited()


@pytest.mark.asyncio
async def test_provisioning_writes_correct_concurrency_and_custom_cap_tag():
    """
    Verify that run_provisioning writes CONCURRENT_CALL_LIMIT with is_custom_cap tag:
    - Default/un-customized sub-org gets tier limit with is_custom_cap=False
    - Custom-capped sub-org gets capped limit with is_custom_cap=True
    """
    # 1. Un-customized sub-org
    master_cust = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None)
    sub_org_normal = Customer(
        id=10,
        dograh_org_id=210,
        status="agent_building",
        billing_org_id=1,
        onboarding_form={}
    )
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=950)
    sub_sub = Subscription(id=10, customer_id=10, plan="pro", per_minute_rate_paise=950)

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id = :id_1" in q_str or "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master_cust if cid == 1 else sub_org_normal
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org_normal
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert, \
         patch("services.dograh_client.upsert_org_config", mock_upsert), \
         patch("services.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await provisioning_service.run_provisioning(10, "pro", mock_db)

    # Verify CONCURRENT_CALL_LIMIT upsert payload
    ccl_calls = [call for call in mock_upsert.call_args_list if call.args[1] == "CONCURRENT_CALL_LIMIT"]
    assert len(ccl_calls) >= 1
    org_id, key, payload = ccl_calls[0].args
    assert org_id == 210
    assert payload["value"] == 10  # Pro tier limit
    assert payload["is_custom_cap"] is False

    # 2. Sub-org with explicit custom cap
    sub_org_custom = Customer(
        id=11,
        dograh_org_id=211,
        status="under_review",
        billing_org_id=1,
        onboarding_form={"concurrent_call_limit": 4}
    )

    async def mock_execute_custom(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id = :id_1" in q_str or "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master_cust if cid == 1 else sub_org_custom
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_org_custom
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute_custom)

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock()) as mock_upsert_custom, \
         patch("services.dograh_client.upsert_org_config", mock_upsert_custom), \
         patch("services.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await provisioning_service.run_provisioning(11, "pro", mock_db)

    ccl_custom_calls = [call for call in mock_upsert_custom.call_args_list if call.args[1] == "CONCURRENT_CALL_LIMIT"]
    assert len(ccl_custom_calls) >= 1
    _, _, custom_payload = ccl_custom_calls[0].args
    assert custom_payload["value"] == 4  # Capped at 4
    assert custom_payload["is_custom_cap"] is True


@pytest.mark.asyncio
async def test_webhook_upgrade_cascades_to_all_suborgs():
    """Verify Razorpay upgrade webhook reprovisions sub-orgs in under_review and agent_building."""
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={})
    sub_review = Customer(id=2, dograh_org_id=200, status="under_review", billing_org_id=1, onboarding_form={})
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)

    mock_db = MagicMock()
    async def mock_exec(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.setup_fee_order_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master_sub if cid == 1 else sub_sub
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_review]
        elif "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_exec)
    mock_db.commit = AsyncMock()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_upg",
                    "order_id": "order_test_upg",
                    "amount": 800000,
                    "notes": {
                        "customer_id": "1",
                        "upgrade_tier": "pro"
                    }
                }
            }
        }
    }

    import json
    body_bytes = json.dumps(payload).encode()
    with patch("services.razorpay_client.verify_webhook_signature", return_value=True), \
         patch("services.billing_service.credit_wallet", AsyncMock()), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.dograh_client.restore_org_calls", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        req = MagicMock()
        req.body = AsyncMock(return_value=body_bytes)
        req.headers = {"X-Razorpay-Signature": "mock_sig"}
        res = await razorpay_webhook(req, db=mock_db)

    assert res["status"] == "ok"
    prov_cids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_cids
    assert 2 in prov_cids, "Sub-org under_review must be reprovisioned on webhook upgrade"
    mock_sync.assert_awaited()


@pytest.mark.asyncio
async def test_request_tier_upgrade_cascades_to_all_suborgs():
    """Verify request_tier_upgrade reprovisions sub-orgs regardless of active status."""
    master = Customer(id=1, dograh_org_id=100, status="active", billing_org_id=None, onboarding_form={}, contact_name="Boss", contact_email="b@co.in")
    sub_review = Customer(id=2, dograh_org_id=200, status="under_review", billing_org_id=1, onboarding_form={})
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=600)
    sub_sub = Subscription(id=2, customer_id=2, plan="starter", per_minute_rate_paise=600)

    mock_db = MagicMock()
    async def mock_exec(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        elif "subscriptions.customer_id =" in q_str:
            try:
                cid = query.whereclause.right.value
                mock_res.scalar_one_or_none.return_value = master_sub if cid == 1 else sub_sub
            except Exception:
                mock_res.scalar_one_or_none.return_value = master_sub
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_review]
        elif "wallets" in q_str.lower():
            mock_res.scalar_one_or_none.return_value = Wallet(id=1, customer_id=1, balance_paise=2000000)
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_exec)
    mock_db.commit = AsyncMock()

    with patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.send_email", AsyncMock()):

        result = await request_tier_upgrade(org_id=100, data=TierUpgradeRequest(requested_tier="pro"), db=mock_db)

    assert result["status"] == "success"
    prov_cids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_cids
    assert 2 in prov_cids, "Sub-org in under_review must be reprovisioned on instant tier upgrade"
    mock_sync.assert_awaited()


@pytest.mark.asyncio
async def test_dograh_call_concurrency_inherits_group_limit_when_uncustomized():
    """
    Verify Issue 10 resolution in Dograh's call_concurrency service:
    When Master upgrades to 10 calls (group_id=100, group_limit=10),
    a sub-org (org_id=200) with is_custom_cap=False (or uncustomized)
    evaluates to 10 (NOT frozen at 2).
    """
    import os
    import sys
    from pathlib import Path
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    voice_agent_path = str(Path(__file__).resolve().parents[3] / "voice-agent")
    if voice_agent_path not in sys.path:
        sys.path.insert(0, voice_agent_path)

    from types import SimpleNamespace
    from api.services.call_concurrency.service import CallConcurrencyService
    from api.enums import OrganizationConfigurationKey

    service = CallConcurrencyService()

    async def mock_get_cfg(org_id, key):
        if org_id == 200 and key == OrganizationConfigurationKey.CONCURRENCY_GROUP_ID.value:
            return SimpleNamespace(value={"value": 100})
        if org_id == 200 and key == OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value:
            # Sub-org still had limit 2 with is_custom_cap=False (un-customized default)
            return SimpleNamespace(value={"value": 2, "is_custom_cap": False})
        if org_id == 100 and key == OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value:
            # Master upgraded to 10
            return SimpleNamespace(value={"value": 10})
        return None

    with patch("api.services.call_concurrency.service.db_client") as mock_db:
        mock_db.get_configuration = AsyncMock(side_effect=mock_get_cfg)

        limit = await service.get_effective_org_concurrent_limit(200)

    # Must evaluate to 10 (Master group limit), NOT frozen at 2!
    assert limit == 10, f"Expected 10 from master group, got {limit}"


@pytest.mark.asyncio
async def test_dograh_call_concurrency_respects_explicit_custom_cap():
    """
    Verify that an explicit custom cap (is_custom_cap=True, e.g. cap=3)
    is respected even when master group has 10 calls.
    """
    import os
    import sys
    from pathlib import Path
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    voice_agent_path = str(Path(__file__).resolve().parents[3] / "voice-agent")
    if voice_agent_path not in sys.path:
        sys.path.insert(0, voice_agent_path)

    from types import SimpleNamespace
    from api.services.call_concurrency.service import CallConcurrencyService
    from api.enums import OrganizationConfigurationKey

    service = CallConcurrencyService()

    async def mock_get_cfg(org_id, key):
        if org_id == 200 and key == OrganizationConfigurationKey.CONCURRENCY_GROUP_ID.value:
            return SimpleNamespace(value={"value": 100})
        if org_id == 200 and key == OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value:
            # Explicit cap of 3
            return SimpleNamespace(value={"value": 3, "is_custom_cap": True})
        if org_id == 100 and key == OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value:
            return SimpleNamespace(value={"value": 10})
        return None

    with patch("api.services.call_concurrency.service.db_client") as mock_db:
        mock_db.get_configuration = AsyncMock(side_effect=mock_get_cfg)

        limit = await service.get_effective_org_concurrent_limit(200)

    # Must be capped at 3
    assert limit == 3
