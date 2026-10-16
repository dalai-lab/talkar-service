import json
import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription, WalletTransaction
from routers.billing import razorpay_webhook, confirm_topup, ConfirmTopupRequest
from config import CALL_BLOCK_THRESHOLD_PAISE


@pytest.mark.asyncio
async def test_suborg_suspended_reactivates_on_webhook_topup():
    """
    Issue 22:
    When a top-up payment succeeds for a sub-org, the webhook must resolve
    the master wallet via get_billing_wallet/billing_org_id, transition the
    sub-org from 'suspended' to 'active', clear suspension reasons, reactivate
    a zero-balance suspended master, run provisioning, and sync wallet block policy.
    """
    mock_db = MagicMock()

    # Database objects
    master = Customer(
        id=1,
        dograh_org_id=101,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance", "suspension_message": "Account balance zero"},
    )
    sub_org = Customer(
        id=2,
        dograh_org_id=202,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance", "suspension_message": "Account balance zero"},
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=100000)
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    customers_by_id = {1: master, 2: sub_org}

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
                mock_res.scalar_one_or_none.return_value = sub_org
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        if "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org]
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_suborg_topup_123",
                    "order_id": "order_suborg_topup_123",
                    "amount": 100000,
                    "notes": {"customer_id": "2"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)),
        patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    # 1. Sub-org status transitioned from suspended to active
    assert sub_org.status == "active"
    assert "suspension_reason" not in sub_org.onboarding_form
    assert "suspension_message" not in sub_org.onboarding_form

    # 2. Master status also reactivated from zero_balance suspension
    assert master.status == "active"
    assert "suspension_reason" not in master.onboarding_form

    # 3. Provisioning ran for sub-org
    mock_prov.assert_awaited_with(sub_org.id, None, mock_db)

    # 4. sync_wallet_block_policy was invoked for sub_org.id
    mock_sync.assert_awaited_once_with(mock_db, sub_org.id)


@pytest.mark.asyncio
async def test_suborg_pending_deposit_activates_on_webhook_topup():
    """
    Issue 22:
    A sub-org in 'pending_deposit' status must transition to 'active',
    have run_provisioning executed, and invoke sync_wallet_block_policy when
    top-up brings the shared balance >= activation_deposit_paise.
    """
    mock_db = MagicMock()

    master = Customer(id=1, dograh_org_id=101, status="active", billing_org_id=None)
    sub_org = Customer(
        id=3,
        dograh_org_id=303,
        status="pending_deposit",
        billing_org_id=1,
        contact_email="branch@example.com",
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=600000)
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=2400)

    customers_by_id = {1: master, 3: sub_org}

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
                mock_res.scalar_one_or_none.return_value = sub_org
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
                    "id": "pay_pending_suborg_123",
                    "order_id": "order_pending_suborg_123",
                    "amount": 600000,
                    "notes": {"customer_id": "3"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)),
        patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
        patch("services.notification_service.send_email", AsyncMock()) as mock_email,
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    assert sub_org.status == "active"
    mock_prov.assert_awaited_with(sub_org.id, None, mock_db)
    mock_sync.assert_awaited_once_with(mock_db, sub_org.id)
    mock_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_topup_reactivates_zero_balance_suspended_suborgs():
    """
    Issue 22:
    When a master customer tops up and is reactivated from 'suspended',
    sub-orgs that were suspended due to 'zero_balance' must also be reactivated
    to 'active', while sub-orgs suspended for other reasons (e.g. TOS violation)
    remain suspended.
    """
    mock_db = MagicMock()

    master = Customer(
        id=1,
        dograh_org_id=101,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"},
    )
    sub_org_zero = Customer(
        id=2,
        dograh_org_id=202,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"},
    )
    sub_org_tos = Customer(
        id=3,
        dograh_org_id=303,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "TOS violation"},
    )

    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)

    customers_by_id = {1: master, 2: sub_org_zero, 3: sub_org_tos}

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
                mock_res.scalar_one_or_none.return_value = master
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_wallet
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master_sub
            return mock_res
        if "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = [sub_org_zero, sub_org_tos]
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_master_topup_456",
                    "order_id": "order_master_topup_456",
                    "amount": 500000,
                    "notes": {"customer_id": "1"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)),
        patch("services.provisioning_service.run_provisioning", AsyncMock()),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    assert master.status == "active"
    assert "suspension_reason" not in master.onboarding_form

    # Sub-org suspended for zero_balance is reactivated
    assert sub_org_zero.status == "active"
    assert "suspension_reason" not in sub_org_zero.onboarding_form

    # Sub-org suspended for TOS violation remains suspended
    assert sub_org_tos.status == "suspended"
    assert sub_org_tos.onboarding_form.get("suspension_reason") == "TOS violation"

    mock_sync.assert_awaited_once_with(mock_db, master.id)


@pytest.mark.asyncio
async def test_suborg_confirm_topup_reactivates_suborg_and_zero_balance_master():
    """
    Issue 22:
    When a sub-org tops up via confirm_topup endpoint, it must also resolve the
    master subscription and wallet, un-suspend the sub-org and master, and sync policy.
    """
    mock_db = MagicMock()

    master = Customer(
        id=1,
        dograh_org_id=101,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"},
    )
    sub_org = Customer(
        id=2,
        dograh_org_id=202,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"},
    )
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=250000)
    master_sub = Subscription(id=1, customer_id=1, plan="starter", per_minute_rate_paise=2400)

    customers_by_id = {1: master, 2: sub_org}

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "wallet_transactions" in q_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
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
        dograh_org_id=202,
        razorpay_order_id="order_sub_confirm_789",
        razorpay_payment_id="pay_sub_confirm_789",
        razorpay_signature="mock_signature",
        amount_paise=250000,
    )

    with (
        patch("services.billing_service.credit_wallet", AsyncMock(return_value=master_wallet)),
        patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov,
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
        patch("services.notification_service.notify_customer_topup_successful", AsyncMock()),
    ):
        res = await confirm_topup(req, db=mock_db)

    assert res["status"] == "ok"
    assert sub_org.status == "active"
    assert "suspension_reason" not in sub_org.onboarding_form
    assert master.status == "active"
    assert "suspension_reason" not in master.onboarding_form
    mock_prov.assert_awaited_with(sub_org.id, None, mock_db)
    mock_sync.assert_awaited_once_with(mock_db, sub_org.id)


@pytest.mark.asyncio
async def test_webhook_topup_with_requested_tier_reactivates_status_and_unblocks():
    """
    Fix 2 / Issue 2:
    When a topup webhook contains requested_tier, it must update the subscription plan
    AND reactivate customer.status from 'pending_deposit' (or 'suspended') to 'active',
    and invoke sync_wallet_block_policy.
    """
    mock_db = MagicMock()

    customer = Customer(
        id=10,
        dograh_org_id=1010,
        status="pending_deposit",
        billing_org_id=None,
        contact_email="owner@agency.com",
    )
    wallet = Wallet(id=10, customer_id=10, balance_paise=1500000)
    sub = Subscription(id=10, customer_id=10, plan="starter", per_minute_rate_paise=2400)

    customers_by_id = {10: customer}

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        mock_res = MagicMock()
        if "wallet_transactions" in q_str:
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
                mock_res.scalar_one_or_none.return_value = customer
            return mock_res
        if "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = wallet
            return mock_res
        if "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
            return mock_res
        if "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = []
            return mock_res
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_upgrade_topup_456",
                    "order_id": "order_upgrade_topup_456",
                    "amount": 1500000,
                    "notes": {"customer_id": "10", "requested_tier": "pro"},
                }
            }
        },
    }

    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode())
    mock_request.headers = {"X-Razorpay-Signature": "valid_sig"}

    with (
        patch("services.razorpay_client.verify_webhook_signature", return_value=True),
        patch("services.billing_service.credit_wallet", AsyncMock(return_value=wallet)),
        patch("services.provisioning_service.run_provisioning", AsyncMock()),
        patch("services.dograh_client.restore_org_calls", AsyncMock()),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()) as mock_sync,
        patch("services.notification_service.send_email", AsyncMock()),
    ):
        res = await razorpay_webhook(mock_request, db=mock_db)

    assert res["status"] == "ok"
    # Tier was upgraded
    assert sub.plan == "pro"
    # Status was reactivated from pending_deposit to active!
    assert customer.status == "active"
    # sync_wallet_block_policy was called to unblock Dograh
    mock_sync.assert_awaited_with(mock_db, 10)

