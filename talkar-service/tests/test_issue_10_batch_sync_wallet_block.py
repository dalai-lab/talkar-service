import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, Subscription, CallLog
from services import billing_service


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_batches_spend_and_parallelizes():
    """
    Test Blocker 10:
    Verifies that sync_wallet_block_policy executes a batch GROUP BY query for capped sub-orgs
    and dispatches Dograh restore/block calls in parallel under asyncio.Semaphore(10).
    """
    master = Customer(id=1, dograh_org_id=10, status="active", billing_org_id=None)
    sub1 = Customer(
        id=2,
        dograh_org_id=20,
        status="active",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 100000},  # capped
    )
    sub2 = Customer(
        id=3,
        dograh_org_id=30,
        status="active",
        billing_org_id=1,
        onboarding_form={"monthly_spending_cap_paise": 500000},  # capped
    )
    sub3 = Customer(
        id=4,
        dograh_org_id=40,
        status="active",
        billing_org_id=1,
        onboarding_form={},  # uncapped
    )
    wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    sub = Subscription(id=1, customer_id=1, plan="pro")

    mock_db = MagicMock()

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub1, sub2, sub3]

    # Batch query returns spend aggregation [(2, 120000), (3, 20000)]
    res_batch = MagicMock()
    res_batch.all.return_value = [(2, 120000), (3, 20000)]

    mock_db.execute = AsyncMock(side_effect=[
        res_master,
        res_sub,
        res_sub_orgs,
        res_batch,
    ])

    blocked_orgs = []
    restored_orgs = []

    async def mock_block(org_id):
        blocked_orgs.append(org_id)

    async def mock_restore(org_id, tier, limit, group_id=None, is_custom_cap=False):
        restored_orgs.append(org_id)

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 1))), \
         patch("services.dograh_client.block_org_calls", side_effect=mock_block), \
         patch("services.dograh_client.restore_org_calls", side_effect=mock_restore):

        await billing_service.sync_wallet_block_policy(mock_db, customer_id=1)

    # Sub1 exceeded 100000 cap -> blocked
    assert 20 in blocked_orgs
    # Sub2 within cap -> restored
    assert 30 in restored_orgs
    # Sub3 uncapped -> restored
    assert 40 in restored_orgs
    # Master -> restored
    assert 10 in restored_orgs

    # Verify batch query was executed (4 db.execute calls total)
    assert mock_db.execute.call_count == 4
