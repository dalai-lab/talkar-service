import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, PhoneNumber, PhoneNumberRequest, TalkarAdmin
from routers.admin import AssignPhoneNumberRequest, ApprovePhoneNumberRequestBody, assign_phone_number, approve_phone_number_request
from services.dograh_client import sync_phone_number_to_dograh


@pytest.mark.asyncio
async def test_sync_phone_number_to_dograh_inserts_config_and_number():
    """Verify that sync_phone_number_to_dograh provisions telephony_configurations and telephony_phone_numbers."""
    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    # Query 1: telephony_configurations check -> None
    cfg_res = MagicMock()
    cfg_res.scalar_one_or_none.return_value = None
    # Query 2: insert telephony_configurations -> returns id 101
    insert_cfg_res = MagicMock()
    insert_cfg_res.scalar_one.return_value = 101
    # Query 3: workflows lookup -> returns 77
    wf_res = MagicMock()
    wf_res.scalar_one_or_none.return_value = 77
    # Query 4: telephony_phone_numbers check -> None
    num_res = MagicMock()
    num_res.scalar_one_or_none.return_value = None
    # Query 5: insert telephony_phone_numbers
    insert_num_res = MagicMock()

    mock_session.execute.side_effect = [cfg_res, insert_cfg_res, wf_res, num_res, insert_num_res]

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("services.dograh_client.DograhSessionLocal", return_value=mock_session_ctx):
        await sync_phone_number_to_dograh(org_id=45, phone_number="+919876543210")

    assert mock_session.execute.await_count == 5
    # Verify insert SQL was called with normalized number and workflow id
    last_call = mock_session.execute.call_args_list[4]
    params = last_call[0][1]
    assert params["org_id"] == 45
    assert params["cfg_id"] == 101
    assert params["normalized"] == "+919876543210"
    assert params["country_code"] == "IN"
    assert params["target_wf_id"] == 77
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_assign_phone_number_syncs_to_dograh():
    """Verify that assigning a phone number calls dograh_client.sync_phone_number_to_dograh with resolved workflow."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    customer = Customer(id=1, dograh_org_id=99, company_name="Test Corp", status="active")
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer

    agent_res = MagicMock()
    agent_res.scalar_one_or_none.return_value = 333

    mock_db.execute.side_effect = [cust_res, agent_res]

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    data = AssignPhoneNumberRequest(number="+919876543210", plivo_number_id="PL123")

    with patch("services.dograh_client.sync_phone_number_to_dograh", AsyncMock()) as mock_sync:
        res = await assign_phone_number(customer_id=1, data=data, db=mock_db, current_admin=admin)

    assert res == {"status": "assigned"}
    mock_db.commit.assert_awaited_once()
    mock_sync.assert_awaited_once_with(99, "+919876543210", workflow_id=333)


@pytest.mark.asyncio
async def test_approve_phone_number_request_syncs_all_numbers():
    """Verify that approving a phone number request syncs each approved number to Dograh."""
    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    req = PhoneNumberRequest(id=5, customer_id=1, status="pending")
    req_res = MagicMock()
    req_res.scalar_one_or_none.return_value = req

    customer = Customer(id=1, dograh_org_id=99, company_name="Test Corp", status="active")
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer

    agent_res = MagicMock()
    agent_res.scalar_one_or_none.return_value = 444

    mock_db.execute.side_effect = [req_res, cust_res, agent_res]

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    data = ApprovePhoneNumberRequestBody(numbers=["+919876543210", "+919876543211"])

    with patch("services.dograh_client.sync_phone_number_to_dograh", AsyncMock()) as mock_sync, \
         patch("services.notification_service.notify_customer_phone_approved", AsyncMock()):
        res = await approve_phone_number_request(request_id=5, data=data, db=mock_db, current_admin=admin)

    assert res == {"status": "approved"}
    assert mock_sync.await_count == 2
    mock_sync.assert_any_await(99, "+919876543210", workflow_id=444)
    mock_sync.assert_any_await(99, "+919876543211", workflow_id=444)


@pytest.mark.asyncio
async def test_assign_customer_phone_number_in_customers_router():
    """Verify that calling assign-phone-number on the customers router also syncs to Dograh."""
    from routers.customers import assign_customer_phone_number, AssignPhoneNumberRequest as CustAssignReq

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.add = MagicMock()

    customer = Customer(id=2, dograh_org_id=120, company_name="Customer 2")
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = customer

    agent_res = MagicMock()
    agent_res.scalar_one_or_none.return_value = 555

    mock_db.execute.side_effect = [cust_res, agent_res]

    admin = TalkarAdmin(id=1, email="admin@talkar.in")
    data = CustAssignReq(number="+919876543999")

    with patch("services.dograh_client.sync_phone_number_to_dograh", AsyncMock()) as mock_sync:
        res = await assign_customer_phone_number(customer_id=2, data=data, db=mock_db, current_admin=admin)

    assert res == {"status": "assigned"}
    mock_db.commit.assert_awaited_once()
    mock_sync.assert_awaited_once_with(120, "+919876543999", workflow_id=555)

