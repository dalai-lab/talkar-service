import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, TalkarAdmin
from routers.admin import update_customer, CustomerUpdateRequest


@pytest.mark.asyncio
async def test_admin_tier_update_reprovisions_pending_deposit_master():
    """Verify Issue 31 / Fix 27: Updating tier of a master in pending_deposit triggers re-provisioning for master and sub-orgs."""
    master = Customer(
        id=1,
        dograh_org_id=10,
        billing_org_id=None,
        status="pending_deposit",
        onboarding_form={}
    )
    sub_org = Customer(
        id=2,
        dograh_org_id=20,
        billing_org_id=1,
        status="pending_deposit",
        onboarding_form={}
    )

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

    with patch("services.provisioning_service.run_provisioning", AsyncMock()) as mock_prov, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        res = await update_customer(customer_id=1, data=req, db=mock_db, current_admin=admin)

    assert res["status"] == "success"

    # Both master (id=1) and sub-org (id=2) must have run_provisioning invoked
    called_ids = [call.args[0] for call in mock_prov.call_args_list]
    assert 1 in called_ids, "Master customer in pending_deposit must be re-provisioned"
    assert 2 in called_ids, "Sub-org must be cascaded"
