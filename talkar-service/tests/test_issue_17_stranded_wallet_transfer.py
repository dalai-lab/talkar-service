import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet, WalletTransaction
from routers.customers import set_billing_account_type, SetBillingAccountTypeRequest


@pytest.mark.asyncio
async def test_blocker_17_transfers_stranded_wallet_balance_on_switch_to_shared():
    """
    Test Blocker 17:
    When an independent customer with a positive wallet balance links to a shared master wallet,
    the customer's wallet is zeroed out, master wallet is credited, and an audited
    WalletTransaction(type='credit_transfer') is created.
    """
    sub_org = Customer(
        id=2,
        contact_email="owner@startup.com",
        dograh_org_id=200,
        dograh_user_id=88,
        billing_org_id=None,
        status="active"
    )
    master = Customer(
        id=1,
        contact_email="owner@startup.com",
        dograh_org_id=100,
        dograh_user_id=88,
        billing_org_id=None,
        status="active"
    )

    c_wallet = Wallet(id=2, customer_id=2, balance_paise=150000)  # ₹1,500 stranded
    m_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)  # ₹5,000 existing

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.add = MagicMock()

    # Query 1: lookup customer (by dograh_org_id)
    res_sub = MagicMock()
    res_sub.scalars.return_value.first.return_value = sub_org

    # Query 2: lookup master (by email)
    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master

    # Query 3: lookup customer's wallet
    res_c_wallet = MagicMock()
    res_c_wallet.scalar_one_or_none.return_value = c_wallet

    # Query 4: lookup master's wallet
    res_m_wallet = MagicMock()
    res_m_wallet.scalar_one_or_none.return_value = m_wallet

    mock_db.execute.side_effect = [res_sub, res_master, res_c_wallet, res_m_wallet]

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.dograh_client.verify_dograh_org_user", AsyncMock(return_value=True)), \
         patch("services.dograh_client.upsert_org_config", AsyncMock()), \
         patch("services.redis_client.invalidate_concurrency_group", AsyncMock()):

        resp = await set_billing_account_type(
            dograh_org_id=200,
            data=SetBillingAccountTypeRequest(account_type="shared"),
            db=mock_db,
            x_talkar_email="owner@startup.com"
        )

        assert resp["status"] == "ok"
    assert resp["account_type"] == "shared"
    assert sub_org.billing_org_id == 1

    # Verify balances transferred
    assert c_wallet.balance_paise == 0
    assert m_wallet.balance_paise == 650000  # 500000 + 150000

    # Verify audited WalletTransaction added
    added_txns = [call.args[0] for call in mock_db.add.call_args_list if call.args and isinstance(call.args[0], WalletTransaction)]
    assert len(added_txns) == 1
    txn = added_txns[0]
    assert txn.customer_id == 1  # Master customer
    assert txn.sub_org_id == 2   # Sub org
    assert txn.dograh_org_id == 200
    assert txn.type == "credit_transfer"
    assert txn.amount_paise == 150000
    assert "₹1500.00" in txn.description
