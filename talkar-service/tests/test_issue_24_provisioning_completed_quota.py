import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription
from routers.billing import check_quota, DograhQuotaRequest
from services.provisioning_service import run_provisioning


@pytest.mark.asyncio
async def test_check_quota_rejected_when_pipeline_provisioning_not_completed():
    """Verify Issue 24 / Fix 26: check_quota rejects call when provisioning_completed is False."""
    customer = Customer(
        id=10,
        dograh_org_id=100,
        contact_email="test@example.com",
        status="active",
        onboarding_form={"provisioning_completed": False},
        billing_org_id=None
    )

    mock_db = MagicMock()
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=cust_res)

    req = DograhQuotaRequest(
        organization_id=100,
        mode="telephony",
        active_calls=1
    )

    res = await check_quota(req, db=mock_db, _token="test_token")

    assert res["has_quota"] is False
    assert res["reason"] == "unprovisioned_pipeline"


@pytest.mark.asyncio
async def test_sub_org_check_quota_rejected_when_master_provisioning_not_completed():
    """Verify Issue 24 / Fix 26: Sub-org check_quota rejects call when master provisioning_completed is False."""
    sub_org = Customer(
        id=20,
        dograh_org_id=200,
        contact_email="sub@example.com",
        status="active",
        billing_org_id=10,
        onboarding_form={"provisioning_completed": True}
    )
    master = Customer(
        id=10,
        dograh_org_id=100,
        status="active",
        billing_org_id=None,
        onboarding_form={"provisioning_completed": False}
    )

    mock_db = MagicMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.dograh_org_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub_org
        elif "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = master
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    req = DograhQuotaRequest(
        organization_id=200,
        mode="telephony",
        active_calls=1
    )

    res = await check_quota(req, db=mock_db, _token="test_token")

    assert res["has_quota"] is False
    assert res["reason"] == "unprovisioned_pipeline"


@pytest.mark.asyncio
async def test_run_provisioning_stamps_provisioning_completed_true():
    """Verify Issue 24 / Fix 26: run_provisioning stamps provisioning_completed: True into onboarding_form."""
    customer = Customer(
        id=10,
        dograh_org_id=100,
        status="active",
        billing_org_id=None,
        onboarding_form={}
    )
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query)
        mock_res = MagicMock()
        if "customers.id =" in q_str:
            mock_res.scalar_one_or_none.return_value = customer
        elif "subscriptions.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = sub
        elif "wallets.customer_id =" in q_str:
            mock_res.scalar_one_or_none.return_value = None
        elif "customers.billing_org_id =" in q_str:
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("services.provisioning_service.dograh_client.upsert_org_config", AsyncMock()), \
         patch("services.provisioning_service.notification_service.notify_admin_customer_ready_for_build", AsyncMock()):
        await run_provisioning(customer_id=10, plan="starter", db=mock_db)

    assert customer.onboarding_form.get("provisioning_completed") is True
