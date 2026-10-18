import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Agent, TalkarAdmin
from routers.admin import get_customer_agents as admin_get_customer_agents, parse_iso_datetime as admin_parse_dt
from routers.customers import get_org_agents, parse_iso_datetime as cust_parse_dt


def test_parse_iso_datetime_variants():
    """Verify ISO string parsing handles +00:00, Z, naive, and invalid formats."""
    # Standard ISO with offset
    dt1 = admin_parse_dt("2026-10-18T18:15:35.825925+00:00")
    assert isinstance(dt1, datetime)
    assert dt1.year == 2026 and dt1.month == 10 and dt1.day == 18
    assert dt1.hour == 18 and dt1.minute == 15 and dt1.second == 35

    # Z suffix
    dt2 = admin_parse_dt("2026-10-18T18:15:35.825925Z")
    assert isinstance(dt2, datetime)
    assert dt2.tzinfo is not None

    # Already datetime
    now_dt = datetime.now(timezone.utc)
    assert admin_parse_dt(now_dt) == now_dt

    # None and empty
    assert admin_parse_dt(None) is None
    assert admin_parse_dt("") is None
    assert admin_parse_dt("not-a-date") is None


@pytest.mark.asyncio
async def test_admin_get_customer_agents_parses_iso_string_to_datetime():
    """Verify that admin get_customer_agents passes parsed datetime object to Agent.built_at."""
    customer = Customer(id=5, dograh_org_id=8)
    admin_user = TalkarAdmin(id=1, email="admin@talkar.ai", role="superadmin")

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    # Step 1: Customer lookup
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    # Step 2: Existing agents
    res_existing = MagicMock()
    res_existing.scalars.return_value.all.return_value = []

    # Step 4: Refetch agents
    mock_agent = Agent(id=1, customer_id=5, dograh_workflow_id=94, name="Test Agent")
    res_refetch = MagicMock()
    res_refetch.scalars.return_value.all.return_value = [mock_agent]

    mock_db.execute.side_effect = [res_cust, res_existing, res_refetch]

    workflows_from_dograh = [
        {
            "id": 94,
            "name": "Baga Beach Resort - Aarti Booking Workflow",
            "status": "active",
            "created_at": "2026-10-18T18:15:35.825925+00:00",
        }
    ]

    with patch("services.dograh_client.get_org_workflows", AsyncMock(return_value=workflows_from_dograh)):
        agents = await admin_get_customer_agents(customer_id=5, db=mock_db, current_admin=admin_user)

    assert len(agents) == 1
    # Verify Agent was added with built_at as datetime
    assert mock_db.add.called
    added_agent = mock_db.add.call_args[0][0]
    assert isinstance(added_agent.built_at, datetime)
    assert added_agent.built_at.year == 2026
    assert added_agent.built_at.isoformat().startswith("2026-10-18T18:15:35")
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_get_customer_agents_rolls_back_on_sync_failure():
    """Verify that if workflow sync throws an error, db.rollback() is awaited and existing agents are returned."""
    customer = Customer(id=5, dograh_org_id=8)
    admin_user = TalkarAdmin(id=1, email="admin@talkar.ai", role="superadmin")

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock(side_effect=SQLAlchemyError("DB commit flush failed"))
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    # Step 1: Customer lookup
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    # Step 2: Existing agents
    res_existing = MagicMock()
    res_existing.scalars.return_value.all.return_value = []

    # Step 4: Refetch existing agents
    mock_agent = Agent(id=1, customer_id=5, dograh_workflow_id=90, name="Existing Agent")
    res_refetch = MagicMock()
    res_refetch.scalars.return_value.all.return_value = [mock_agent]

    mock_db.execute.side_effect = [res_cust, res_existing, res_refetch]

    workflows_from_dograh = [
        {
            "id": 94,
            "name": "Failing Workflow",
            "status": "active",
            "created_at": "2026-10-18T18:15:35.825925+00:00",
        }
    ]

    with patch("services.dograh_client.get_org_workflows", AsyncMock(return_value=workflows_from_dograh)):
        agents = await admin_get_customer_agents(customer_id=5, db=mock_db, current_admin=admin_user)

    # Must have called rollback on failure
    mock_db.rollback.assert_awaited_once()
    assert len(agents) == 1
    assert agents[0].name == "Existing Agent"


@pytest.mark.asyncio
async def test_customer_get_agents_parses_iso_string_to_datetime():
    """Verify that customer get_org_agents parses ISO string to datetime and rolls back on failure."""
    customer = Customer(id=5, dograh_org_id=8)

    mock_db = MagicMock()
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.add = MagicMock()

    # Step 1: Customer lookup
    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer

    # Step 2: Existing agents
    res_existing = MagicMock()
    res_existing.scalars.return_value.all.return_value = []

    # Step 3: Refetch agents
    mock_agent = Agent(id=1, customer_id=5, dograh_workflow_id=94, name="Test Agent", crm_link="https://crm.com")
    res_refetch = MagicMock()
    res_refetch.scalars.return_value.all.return_value = [mock_agent]

    mock_db.execute.side_effect = [res_cust, res_existing, res_refetch]

    workflows_from_dograh = [
        {
            "id": 94,
            "name": "Baga Beach Resort - Aarti Booking Workflow",
            "status": "active",
            "created_at": "2026-10-18T18:15:35.825925+00:00",
        }
    ]

    with patch("services.dograh_client.get_org_workflows", AsyncMock(return_value=workflows_from_dograh)), \
         patch("routers.customers.verify_customer_access", AsyncMock()):
        agents = await get_org_agents(dograh_org_id=8, db=mock_db)

    assert len(agents) == 1
    assert agents[0]["dograh_agent_id"] == 94
    added_agent = mock_db.add.call_args[0][0]
    assert isinstance(added_agent.built_at, datetime)
    assert added_agent.built_at.year == 2026
    mock_db.commit.assert_awaited_once()
    assert isinstance(added_agent.built_at, datetime)
    mock_db.commit.assert_awaited_once()
