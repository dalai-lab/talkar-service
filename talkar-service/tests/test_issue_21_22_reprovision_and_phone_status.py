import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, PhoneNumberRequest, TalkarAdmin, Subscription
from routers.billing import confirm_topup, ConfirmTopupRequest
from routers.admin import approve_phone_number_request, ApprovePhoneNumberRequestBody, assign_phone_number, AssignPhoneNumberRequest


@pytest.mark.asyncio
async def test_blocker_21_reprovision_cascaded_sub_orgs_on_reactivation():
    """
    Test Blocker 21:
    When a suspended master wallet is topped up, cascading reactivation to suspended sub-orgs
    invokes run_provisioning for each reactivated sub-org.
    """
    master = Customer(
        id=1,
        dograh_org_id=100,
        status="suspended",
        billing_org_id=None,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub1 = Customer(
        id=2,
        dograh_org_id=200,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub2 = Customer(
        id=3,
        dograh_org_id=300,
        status="suspended",
        billing_org_id=1,
        onboarding_form={"suspension_reason": "zero_balance"}
    )
    sub3_rogue = Customer(
        id=4,
        dograh_org_id=400,
        status="suspended",
        billing_org_id=1,
        onboarding_form={}  # null suspension reason
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        if "where customers.billing_org_id" in q_str:
            res.scalars.return_value.all.return_value = [sub1, sub2, sub3_rogue]
        elif "from customers" in q_str:
            res.scalar_one_or_none.return_value = master
            res.scalars.return_value.first.return_value = master
        elif "from wallets" in q_str:
            res.scalar_one_or_none.return_value = wallet
        elif "from wallet_transactions" in q_str:
            res.scalar_one_or_none.return_value = None
        elif "from subscriptions" in q_str:
            res.scalar_one_or_none.return_value = Subscription(customer_id=1, plan="starter")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.billing.billing_service.credit_wallet", AsyncMock(return_value=wallet)), \
         patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_topup_successful", AsyncMock()):

        req_data = ConfirmTopupRequest(
            dograh_org_id=100,
            razorpay_payment_id="pay_123",
            razorpay_order_id="order_123",
            razorpay_signature="mock_signature",
            amount_paise=500000
        )
        res = await confirm_topup(data=req_data, db=mock_db)

    assert res["status"] == "ok"
    assert master.status == "active"
    assert sub1.status == "active"
    assert sub2.status == "active"
    # Rogue or null reason sub-org must remain suspended (Blocker 30)
    assert sub3_rogue.status == "suspended"

    # Verify run_provisioning was called for master AND both reactivated child sub-orgs, not the rogue one
    prov_called_ids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in prov_called_ids  # master
    assert 2 in prov_called_ids  # sub1
    assert 3 in prov_called_ids  # sub2
    assert 4 not in prov_called_ids  # sub3_rogue not reactivated


@pytest.mark.asyncio
async def test_blocker_22_rejects_phone_approval_if_customer_not_active():
    """
    Test Blocker 22:
    approve_phone_number_request rejects with HTTP 400 if customer status is not 'active'.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    req = PhoneNumberRequest(id=10, customer_id=5, status="pending")

    # 1. Suspended customer
    suspended_cust = Customer(id=5, status="suspended", dograh_org_id=500)
    mock_db = MagicMock()
    res_req = MagicMock()
    res_req.scalar_one_or_none.return_value = req
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = suspended_cust
    mock_db.execute = AsyncMock(side_effect=[res_req, res_cust])

    with pytest.raises(HTTPException) as exc_info:
        await approve_phone_number_request(
            request_id=10,
            data=ApprovePhoneNumberRequestBody(numbers=["+919999999999"]),
            db=mock_db,
            current_admin=admin
        )
    assert exc_info.value.status_code == 400
    assert "Customer must be active" in exc_info.value.detail

    # 2. Churned customer
    churned_cust = Customer(id=5, status="churned", dograh_org_id=500)
    mock_db2 = MagicMock()
    res_req2 = MagicMock()
    res_req2.scalar_one_or_none.return_value = req
    res_cust2 = MagicMock()
    res_cust2.scalar_one_or_none.return_value = churned_cust
    mock_db2.execute = AsyncMock(side_effect=[res_req2, res_cust2])

    with pytest.raises(HTTPException) as exc_info:
        await approve_phone_number_request(
            request_id=10,
            data=ApprovePhoneNumberRequestBody(numbers=["+919999999999"]),
            db=mock_db2,
            current_admin=admin
        )
    assert exc_info.value.status_code == 400
    assert "Customer must be active" in exc_info.value.detail


@pytest.mark.asyncio
async def test_blocker_22_rejects_assign_phone_number_if_customer_not_active():
    """
    Test Blocker 22:
    assign_phone_number rejects with HTTP 400 if customer status is not 'active'.
    """
    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    suspended_cust = Customer(id=5, status="suspended", dograh_org_id=500)

    mock_db = MagicMock()
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = suspended_cust
    mock_db.execute = AsyncMock(return_value=res_cust)

    with pytest.raises(HTTPException) as exc_info:
        await assign_phone_number(
            customer_id=5,
            data=AssignPhoneNumberRequest(number="+919999999999"),
            db=mock_db,
            current_admin=admin
        )
    assert exc_info.value.status_code == 400
    assert "Customer must be active" in exc_info.value.detail
