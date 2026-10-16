import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, WalletTransaction
from routers.billing import DograhDeductRequest, DograhQuotaRequest, deduct_for_run, check_quota
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_get_billing_wallet_behavior_with_suspended_master():
    """get_billing_wallet raises ValueError when allow_suspended=False, but succeeds when allow_suspended=True."""
    mock_db = MagicMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    master_customer = Customer(id=10, dograh_org_id=100, status="suspended", billing_org_id=None)
    wallet = Wallet(id=1, customer_id=10, balance_paise=50000)

    # First call to db.execute selects sub_customer, second selects master_customer, third selects wallet
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub_customer

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master_customer

    res_wallet = MagicMock()
    res_wallet.scalar_one_or_none.return_value = wallet

    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master, res_wallet])

    # 1. allow_suspended=False (default) should raise ValueError
    with pytest.raises(ValueError, match="Master organization 10 is suspended"):
        await billing_service.get_billing_wallet(mock_db, sub_customer.id, allow_suspended=False)

    # 2. allow_suspended=True should return (wallet, master_id)
    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master, res_wallet])
    w, m_id = await billing_service.get_billing_wallet(mock_db, sub_customer.id, allow_suspended=True)
    assert w == wallet
    assert m_id == 10


@pytest.mark.asyncio
async def test_check_quota_when_master_is_suspended():
    """check_quota cleanly rejects with master_suspended when master is suspended, avoiding 500 crashes."""
    mock_db = MagicMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    master_customer = Customer(id=10, dograh_org_id=100, status="suspended", billing_org_id=None)

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub_customer

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master_customer

    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master])

    req = DograhQuotaRequest(organization_id=200, mode="telephony")
    res = await check_quota(data=req, db=mock_db, _token="test_token")

    assert res == {"has_quota": False, "reason": "master_suspended"}


@pytest.mark.asyncio
async def test_fix_17_check_quota_agent_building_rejected_when_master_is_suspended():
    """
    Fix 17 / Issue 13:
    When a sub-org is in agent_building and requests playground bypass (webrtc/textchat),
    check_quota must evaluate master suspension FIRST and reject with master_suspended,
    preventing suspended accounts from generating free traffic.
    """
    mock_db = MagicMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="agent_building", billing_org_id=10)
    master_customer = Customer(id=10, dograh_org_id=100, status="suspended", billing_org_id=None)

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub_customer

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master_customer

    mock_db.execute = AsyncMock(side_effect=[res_sub, res_master])

    req = DograhQuotaRequest(organization_id=200, mode="webrtc", active_calls=1)
    res = await check_quota(data=req, db=mock_db, _token="test_token")

    assert res == {"has_quota": False, "reason": "master_suspended"}


@pytest.mark.asyncio
async def test_check_quota_catches_value_error_from_get_billing_wallet():
    """check_quota fallback catches ValueError from get_billing_wallet without crashing."""
    mock_db = MagicMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub_customer
    mock_db.execute = AsyncMock(return_value=res_sub)

    req = DograhQuotaRequest(organization_id=200, mode="telephony")

    with patch("services.billing_service.get_billing_wallet", AsyncMock(side_effect=ValueError("Master organization 10 is suspended"))):
        res = await check_quota(data=req, db=mock_db, _token="test_token")

    assert res == {"has_quota": False, "reason": "master_suspended"}


@pytest.mark.asyncio
async def test_deduct_for_run_allows_deduction_when_master_is_suspended():
    """deduct_for_run successfully debits master wallet for completed calls even if master is suspended."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=98800)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_customer

    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_update = MagicMock()
    res_update.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[
        res_cust,   # lookup customer by dograh_org_id
        res_log,    # check duplicate CallLog
        res_sub,    # lookup subscription
        res_update, # atomic update wallet
        res_update, # refresh wallet query
    ])

    req = DograhDeductRequest(
        workflow_run_id=901,
        duration_seconds=120,  # 2 minutes = 1200 paise
        organization_id=200,
        mode="telephony",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))) as mock_get_wallet, \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()) as mock_recharge, \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        res = await deduct_for_run(data=req, db=mock_db, _token="test_token")

    assert res["status"] == "ok"
    assert res["cost_paise"] == 1200
    mock_get_wallet.assert_awaited_once_with(mock_db, 20, allow_suspended=True)
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_deduct_for_run_catches_value_error_and_returns_200():
    """If get_billing_wallet raises ValueError, deduct_for_run catches it, commits CallLog, and returns master_suspended."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_customer
    res_log = MagicMock()
    res_log.scalar_one_or_none.return_value = None
    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    mock_db.execute = AsyncMock(side_effect=[res_cust, res_log, res_sub])

    req = DograhDeductRequest(
        workflow_run_id=902,
        duration_seconds=60,
        organization_id=200,
        mode="telephony",
    )

    with patch("services.billing_service.get_billing_wallet", AsyncMock(side_effect=ValueError("Master organization 10 is suspended"))):
        res = await deduct_for_run(data=req, db=mock_db, _token="test_token")

    assert res["status"] == "master_suspended"
    assert res["cost_paise"] == 600
    assert "Master organization 10 is suspended" in res["reason"]
    # CallLog should still be committed for auditability
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_cron_deduct_for_run_allows_suspended_master():
    """services.billing_service.deduct_for_run passes allow_suspended=True and debits wallet."""
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    sub = Subscription(id=1, customer_id=10, plan="starter", per_minute_rate_paise=600)
    wallet = Wallet(id=1, customer_id=10, balance_paise=500000)
    wallet_post = Wallet(id=1, customer_id=10, balance_paise=499400)

    run_payload = {
        "id": 888,
        "organization_id": 200,
        "workflow_id": None,
        "duration_seconds": 60,
        "mode": "telephony",
    }

    res_run = MagicMock()
    res_run.scalar_one_or_none.return_value = None  # no existing call_log

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = sub_customer

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_update = MagicMock()
    res_update.scalar_one_or_none.return_value = wallet_post

    mock_db.execute = AsyncMock(side_effect=[
        res_cust,   # customer lookup by dograh_org_id
        res_sub,    # subscription lookup
        res_run,    # existing call log check
        res_update, # wallet update
        res_update, # wallet refresh
    ])

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))) as mock_get_wallet, \
         patch("services.dograh_client.get_run", AsyncMock(return_value=run_payload)), \
         patch("db.session.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()), \
         patch("services.notification_service.notify_customer_service_paused", AsyncMock()):
        await billing_service.deduct_for_run(run_id=888)

    mock_get_wallet.assert_awaited_once_with(mock_db, 20, allow_suspended=True)
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_sync_wallet_block_policy_blocks_suspended_master_and_sub_orgs():
    """sync_wallet_block_policy retrieves wallet via allow_suspended=True and blocks calls when master is suspended."""
    mock_db = MagicMock()

    master_customer = Customer(id=10, dograh_org_id=100, status="suspended", billing_org_id=None)
    sub_customer = Customer(id=20, dograh_org_id=200, status="active", billing_org_id=10)
    sub = Subscription(id=1, customer_id=10, plan="starter")
    wallet = Wallet(id=1, customer_id=10, balance_paise=100000)

    res_master = MagicMock()
    res_master.scalar_one_or_none.return_value = master_customer

    res_sub = MagicMock()
    res_sub.scalar_one_or_none.return_value = sub

    res_sub_orgs = MagicMock()
    res_sub_orgs.scalars.return_value.all.return_value = [sub_customer]

    mock_db.execute = AsyncMock(side_effect=[
        res_master,   # lookup master
        res_sub,      # lookup subscription
        res_sub_orgs, # lookup sub orgs
    ])

    with patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))) as mock_get_wallet, \
         patch("services.dograh_client.block_org_calls", AsyncMock()) as mock_block:
        await billing_service.sync_wallet_block_policy(mock_db, customer_id=20)

    mock_get_wallet.assert_awaited_once_with(mock_db, 20, allow_suspended=True)
    assert mock_block.await_count == 2
    mock_block.assert_any_await(100)
    mock_block.assert_any_await(200)

