import json
import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, TalkarAdmin
from services import billing_service
from routers.billing import confirm_topup, ConfirmTopupRequest, razorpay_webhook
from routers.admin import manual_credit_grant, CreditGrantRequest
from routers.automation import credit_customer, CreditRequest


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_maintains_block_on_suspended_suborg_when_master_active():
    """
    Issue 32:
    When Master is active with ample wallet balance, sync_wallet_block_policy
    must restore Master and active sub-orgs, but MUST block any sub-org whose
    status is 'suspended' (e.g., fraud, TOS violation), NEVER restoring calls for it.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_active = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    sub_suspended = Customer(
        id=3,
        dograh_org_id=30,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "fraud"}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="starter")

    mock_db = MagicMock()
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub_active, sub_suspended]

    mock_db.execute = AsyncMock(side_effect=[
        res_master,
        res_sub,
        res_sub_orgs,
    ])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore:

        await billing_service.sync_wallet_block_policy(mock_db, customer_id=1)

    # 1. Suspended sub-org (org 30) must be blocked in Dograh
    mock_block.assert_awaited_once_with(30)

    # 2. Master (org 10) and active sub-org (org 20) must be restored
    assert mock_restore.await_count == 2
    restored_org_ids = [call.args[0] for call in mock_restore.await_args_list]
    assert 10 in restored_org_ids
    assert 20 in restored_org_ids
    assert 30 not in restored_org_ids, "Suspended sub-org must NEVER be restored"


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_invoked_from_suspended_suborg_id():
    """
    Issue 32:
    Even when sync_wallet_block_policy is called with customer_id=sub_suspended.id,
    it must resolve the master wallet, restore master & sister sub-org, and block
    the suspended sub-org.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_active = Customer(id=2, dograh_org_id=20, status="active", billing_org_id=1, onboarding_form={})
    sub_suspended = Customer(
        id=3,
        dograh_org_id=30,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "terms_violation"}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="starter")

    mock_db = MagicMock()
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub_active, sub_suspended]

    mock_db.execute = AsyncMock(side_effect=[
        res_master,
        res_sub,
        res_sub_orgs,
    ])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block, \
         patch("services.dograh_client.restore_org_calls", AsyncMock()) as mock_restore:

        await billing_service.sync_wallet_block_policy(mock_db, customer_id=3)

    mock_block.assert_awaited_once_with(30)
    restored_org_ids = [call.args[0] for call in mock_restore.await_args_list]
    assert 10 in restored_org_ids
    assert 20 in restored_org_ids
    assert 30 not in restored_org_ids


@pytest.mark.asyncio
async def test_confirm_topup_does_not_reactivate_suborg_suspended_for_fraud():
    """
    Issue 32:
    Calling confirm_topup for a sub-org suspended for 'fraud' must NOT
    transition it to 'active'. It must remain 'suspended'.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_suspended = Customer(
        id=2,
        dograh_org_id=20,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "fraud", "suspension_message": "Account suspended for fraud"}
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=100000)
    master_sub = Subscription(id=1, customer_id=1, plan="starter")

    customers_by_id = {1: master, 2: sub_suspended}

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_suspended
            return mock_res
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                val = customers_by_id.get(cid)
                mock_res.scalar_one_or_none.return_value = val
            except Exception:
                mock_res.scalar_one_or_none.return_value = master
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = ConfirmTopupRequest(
        dograh_org_id=20,
        razorpay_order_id="order_sub_fraud_123",
        razorpay_payment_id="pay_sub_fraud_123",
        razorpay_signature="mock_sig",
        amount_paise=100000,
    )

    with patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        res = await confirm_topup(req, db=mock_db)

    assert res["status"] == "ok"
    assert sub_suspended.status == "suspended", "Sub-org suspended for fraud must NOT be reactivated on top-up"
    assert sub_suspended.onboarding_form["suspension_reason"] == "fraud"
    mock_prov.assert_not_awaited()
    mock_sync.assert_awaited_once_with(mock_db, sub_suspended.id)


@pytest.mark.asyncio
async def test_webhook_topup_does_not_reactivate_suborg_suspended_for_tos():
    """
    Issue 32:
    Webhook payment.captured for a sub-org suspended for 'TOS violation'
    must NOT transition it to 'active'.
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub_suspended = Customer(
        id=2,
        dograh_org_id=20,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "TOS violation"}
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=100000)
    master_sub = Subscription(id=1, customer_id=1, plan="starter")

    customers_by_id = {1: master, 2: sub_suspended}

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "pg_advisory_xact_lock" in q_str or "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        if "customers.setup_fee_order_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        if "customers.id =" in q_str:
            try:
                cid = query.whereclause.right.value
                val = customers_by_id.get(cid)
                mock_res.scalar_one_or_none.return_value = val
            except Exception:
                mock_res.scalar_one_or_none.return_value = sub_suspended
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_tos_sub_123",
                    "order_id": "order_tos_sub_123",
                    "amount": 100000,
                    "notes": {"customer_id": "2"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with patch("services.razorpay_client.verify_webhook_signature", return_value=True), \
         patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync:

        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    assert sub_suspended.status == "suspended", "Sub-org suspended for TOS violation must NOT be reactivated on webhook"
    assert sub_suspended.onboarding_form["suspension_reason"] == "TOS violation"
    mock_prov.assert_not_awaited()
    mock_sync.assert_awaited_once_with(mock_db, sub_suspended.id)


@pytest.mark.asyncio
async def test_admin_credit_does_not_reactivate_suborg_suspended_for_fraud():
    """
    Issue 32:
    Calling manual_credit_grant on an administratively suspended sub-org
    must NOT reactivate it.
    """
    sub_suspended = Customer(
        id=2,
        dograh_org_id=20,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "fraud"}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=100000)
    sub = Subscription(id=1, customer_id=1, plan="starter")

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "update wallets" in q_str:
            mock_res.scalar_one_or_none.return_value = wallet
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = wallet
            return mock_res
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_suspended
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    admin = TalkarAdmin(id=1, email="admin@talkar.in", name="Admin", role="superadmin", password_hash="dummy")
    req = CreditGrantRequest(amount_paise=50000, description="Manual credit bonus")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_credit_granted", AsyncMock()):

        res = await manual_credit_grant(2, req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"
    assert sub_suspended.status == "suspended", "Admin credit must NOT reactivate a fraud-suspended sub-org"
    mock_prov.assert_not_awaited()
    mock_sync.assert_awaited_once_with(mock_db, 2)


@pytest.mark.asyncio
async def test_automation_credit_does_not_reactivate_suborg_suspended_for_fraud():
    """
    Issue 32:
    Calling credit_customer on an administratively suspended sub-org
    must NOT reactivate it.
    """
    sub_suspended = Customer(
        id=2,
        dograh_org_id=20,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "fraud"}
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=100000)
    sub = Subscription(id=1, customer_id=1, plan="starter")

    mock_db = MagicMock()
    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "update wallets" in q_str:
            mock_res.scalar_one_or_none.return_value = wallet
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = wallet
            return mock_res
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_suspended
            return mock_res
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_suspended
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    req = CreditRequest(amount_rupees=500.0, description="Automation refund")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_credit_granted", AsyncMock()):

        res = await credit_customer(20, req, db=mock_db, api_key=MagicMock())

    assert res["status"] == "success"
    assert sub_suspended.status == "suspended", "Automation credit must NOT reactivate a fraud-suspended sub-org"
    mock_prov.assert_not_awaited()
    mock_sync.assert_awaited_once_with(mock_db, 2)
