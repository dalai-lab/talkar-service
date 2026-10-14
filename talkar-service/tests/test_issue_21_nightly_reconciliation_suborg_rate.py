import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Subscription, Wallet, CallLog, WalletTransaction, Agent
from services.billing_service import deduct_for_run


@pytest.mark.asyncio
async def test_sub_org_reconciliation_bills_at_master_pro_rate():
    """
    Issue 21: When a sub-org has no subscription of its own and runs under a Pro master (1800 paise/min),
    nightly reconciliation in deduct_for_run must bill at the master's Pro rate (1800 paise/min),
    NOT default to the starter rate (600 paise/min).
    """
    master = Customer(id=1, dograh_org_id=101, status="active", billing_org_id=None)
    sub_org = Customer(id=20, billing_org_id=1, dograh_org_id=102, status="active", onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)

    db = AsyncMock()

    # Query 1: select Customer by dograh_org_id
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = sub_org

    # Query 2: select master Subscription (customer.billing_org_id or customer.id -> 1)
    sub_res = MagicMock()
    sub_res.scalar_one_or_none.return_value = master_sub

    # Query 3: CallLog idempotency check (not previously processed)
    cl_res = MagicMock()
    cl_res.scalar_one_or_none.return_value = None

    # Query 4: Agent lookup
    agent_res1 = MagicMock()
    agent_res1.scalar_one_or_none.return_value = None
    agent_res2 = MagicMock()
    agent_res2.scalar_one_or_none.return_value = None

    # Query 5-7: get_billing_wallet
    bw_c1 = MagicMock()
    bw_c1.scalar_one_or_none.return_value = sub_org
    bw_c2 = MagicMock()
    bw_c2.scalar_one_or_none.return_value = master
    bw_w = MagicMock()
    bw_w.scalar_one_or_none.return_value = master_wallet

    # Query 8: update(Wallet)
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=496400)
    update_res = MagicMock()
    update_res.scalar_one_or_none.return_value = updated_wallet

    # Query 9: re-fetch wallet after auto-recharge
    refreshed_res = MagicMock()
    refreshed_res.scalar_one_or_none.return_value = updated_wallet

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "FROM customers" in q_str:
            if "dograh_org_id" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            elif "WHERE customers.id = :id_1" in q_str or "WHERE customers.id = 20" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            else:
                res.scalar_one_or_none.return_value = master
            return res
        elif "FROM subscriptions" in q_str:
            res.scalar_one_or_none.return_value = master_sub
            return res
        elif "FROM call_logs" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM agents" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        elif "UPDATE wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=side_effect_execute)

    run_data = {
        "organization_id": 102,
        "duration_seconds": 120,  # 2 minutes
        "mode": "telephony",
        "workflow_id": 999
    }

    added_objects = []
    db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

    with patch("services.dograh_client.get_run", AsyncMock(return_value=run_data)), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        await deduct_for_run(run_id=8801, db=db)

    # 1. Assert CallLog
    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    # 2 minutes * 1800 paise/min = 3600 paise (NOT 1200 paise)
    assert call_logs[0].cost_to_customer_paise == 3600
    assert call_logs[0].plan == "pro"

    # 2. Assert WalletTransaction
    transactions = [o for o in added_objects if isinstance(o, WalletTransaction)]
    assert len(transactions) == 1
    assert transactions[0].customer_id == 1
    assert transactions[0].sub_org_id == 20
    assert transactions[0].amount_paise == -3600


@pytest.mark.asyncio
async def test_sub_org_reconciliation_bills_at_master_custom_rate():
    """
    Issue 21: When master account is on a Custom Plan (e.g. 2500 paise/min),
    nightly reconciliation must bill sub-org calls at the custom rate (2500 paise/min),
    NOT default to the starter rate (600 paise/min).
    """
    master = Customer(id=5, dograh_org_id=501, status="active", billing_org_id=None)
    sub_org = Customer(id=55, billing_org_id=5, dograh_org_id=502, status="active", onboarding_form={})

    master_sub = Subscription(
        id=5,
        customer_id=5,
        plan="custom",
        per_minute_rate_paise=2500,
        custom_config={"tts_provider": "elevenlabs"}
    )
    master_wallet = Wallet(id=5, customer_id=5, balance_paise=1000000)
    updated_wallet = Wallet(id=5, customer_id=5, balance_paise=997500)

    db = AsyncMock()

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "FROM customers" in q_str:
            if "dograh_org_id" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            elif "WHERE customers.id = :id_1" in q_str or "WHERE customers.id = 55" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            else:
                res.scalar_one_or_none.return_value = master
            return res
        elif "FROM subscriptions" in q_str:
            res.scalar_one_or_none.return_value = master_sub
            return res
        elif "FROM call_logs" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM agents" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        elif "UPDATE wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=side_effect_execute)

    run_data = {
        "organization_id": 502,
        "duration_seconds": 60,  # 1 minute
        "mode": "telephony",
        "workflow_id": 999
    }

    added_objects = []
    db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

    with patch("services.dograh_client.get_run", AsyncMock(return_value=run_data)), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        await deduct_for_run(run_id=8802, db=db)

    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    # 1 minute * 2500 paise/min = 2500 paise
    assert call_logs[0].cost_to_customer_paise == 2500
    assert call_logs[0].plan == "custom"


@pytest.mark.asyncio
async def test_sub_org_reconciliation_honors_approved_starter_tier_under_pro_master():
    """
    Issue 19 & 21 Alignment: When a sub-org is approved on Starter (600 paise/min) under a Pro master (1800 paise/min),
    nightly reconciliation bills at the approved Starter rate (600 paise/min).
    """
    master = Customer(id=1, dograh_org_id=101, status="active", billing_org_id=None)
    sub_org = Customer(
        id=20,
        billing_org_id=1,
        dograh_org_id=102,
        status="active",
        onboarding_form={"approved_tier": "starter"}
    )

    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)
    master_wallet = Wallet(id=1, customer_id=1, balance_paise=500000)
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=499400)

    db = AsyncMock()

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "FROM customers" in q_str:
            if "dograh_org_id" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            elif "WHERE customers.id = :id_1" in q_str or "WHERE customers.id = 20" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            else:
                res.scalar_one_or_none.return_value = master
            return res
        elif "FROM subscriptions" in q_str:
            res.scalar_one_or_none.return_value = master_sub
            return res
        elif "FROM call_logs" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM agents" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        elif "UPDATE wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=side_effect_execute)

    run_data = {
        "organization_id": 102,
        "duration_seconds": 60,  # 1 minute
        "mode": "telephony",
        "workflow_id": 999
    }

    added_objects = []
    db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

    with patch("services.dograh_client.get_run", AsyncMock(return_value=run_data)), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        await deduct_for_run(run_id=8803, db=db)

    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    # 1 minute * 600 paise/min = 600 paise
    assert call_logs[0].cost_to_customer_paise == 600
    assert call_logs[0].plan == "starter"


@pytest.mark.asyncio
async def test_sub_org_reconciliation_honors_agent_custom_rate():
    """
    Issue 21: When an agent has an explicit per_minute_rate_paise override,
    nightly reconciliation bills at the agent's custom rate regardless of tier.
    """
    master = Customer(id=1, dograh_org_id=101, status="active", billing_org_id=None)
    sub_org = Customer(id=20, billing_org_id=1, dograh_org_id=102, status="active", onboarding_form={})

    master_sub = Subscription(id=1, customer_id=1, plan="pro", per_minute_rate_paise=1800)
    agent = Agent(id=77, customer_id=20, dograh_workflow_id=999, per_minute_rate_paise=3000)
    updated_wallet = Wallet(id=1, customer_id=1, balance_paise=497000)

    db = AsyncMock()

    def side_effect_execute(query, *args, **kwargs):
        q_str = str(query)
        res = MagicMock()
        if "FROM customers" in q_str:
            if "dograh_org_id" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            elif "WHERE customers.id = :id_1" in q_str or "WHERE customers.id = 20" in q_str:
                res.scalar_one_or_none.return_value = sub_org
            else:
                res.scalar_one_or_none.return_value = master
            return res
        elif "FROM subscriptions" in q_str:
            res.scalar_one_or_none.return_value = master_sub
            return res
        elif "FROM call_logs" in q_str:
            res.scalar_one_or_none.return_value = None
            return res
        elif "FROM agents" in q_str:
            res.scalar_one_or_none.return_value = agent
            return res
        elif "FROM wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        elif "UPDATE wallets" in q_str:
            res.scalar_one_or_none.return_value = updated_wallet
            return res
        res.scalar_one_or_none.return_value = None
        return res

    db.execute = AsyncMock(side_effect=side_effect_execute)

    run_data = {
        "organization_id": 102,
        "duration_seconds": 60,  # 1 minute
        "mode": "telephony",
        "workflow_id": 999
    }

    added_objects = []
    db.add = MagicMock(side_effect=lambda o: added_objects.append(o))

    with patch("services.dograh_client.get_run", AsyncMock(return_value=run_data)), \
         patch("services.billing_service.check_and_trigger_auto_recharge", AsyncMock()), \
         patch("services.billing_service.sync_wallet_block_policy", AsyncMock()):
        await deduct_for_run(run_id=8805, db=db)

    call_logs = [o for o in added_objects if isinstance(o, CallLog)]
    assert len(call_logs) == 1
    # 1 minute * 3000 paise/min = 3000 paise
    assert call_logs[0].cost_to_customer_paise == 3000
