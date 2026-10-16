import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer
from routers.customers import submit_onboarding_by_org


@pytest.mark.asyncio
async def test_fix_20_dynamic_onboarding_links_to_established_master():
    """
    Test Fix 20 (Issue 17):
    When a customer record does not exist during onboarding submission,
    resolve an established master customer with the matching contact_email
    and set billing_org_id to the master's ID.
    """
    master = Customer(
        id=42,
        dograh_org_id=4200,
        contact_email="owner@agency.com",
        company_name="Master Agency",
        billing_org_id=None,
        status="active"
    )

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    added_customers = []
    def mock_add(obj):
        added_customers.append(obj)
    mock_db.add = mock_add

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        res = MagicMock()
        # Query 1: lookup customer by dograh_org_id (returns None -> triggers dynamic creation)
        if "where customers.dograh_org_id ==" in q_str or "customers.dograh_org_id = :dograh_org_id" in q_str:
            res.scalar_one_or_none.return_value = None
        # Query 2: lookup established master by contact_email
        elif "where customers.contact_email ==" in q_str or "customers.contact_email = :contact_email" in q_str:
            res.scalar_one_or_none.return_value = master
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.notification_service.send_email", AsyncMock()):

        result = await submit_onboarding_by_org(
            dograh_org_id=500,
            data={
                "form": {
                    "email": "owner@agency.com",
                    "businessName": "Sub Brand 1",
                    "pocName": "Manager",
                }
            },
            db=mock_db
        )

    assert result.dograh_org_id == 500
    assert result.company_name == "Sub Brand 1"
    assert result.contact_email == "owner@agency.com"
    # Preserves billing hierarchy!
    assert result.billing_org_id == 42
    assert result.status == "under_review"
    assert len(added_customers) == 1
    assert added_customers[0].billing_org_id == 42


@pytest.mark.asyncio
async def test_fix_20_dynamic_onboarding_independent_when_no_established_master():
    """
    When no established master exists for the contact_email,
    billing_org_id remains None (independent).
    """
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    added_customers = []
    mock_db.add = lambda obj: added_customers.append(obj)

    async def mock_execute(query, *args, **kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("routers.customers.verify_customer_access", AsyncMock()), \
         patch("services.notification_service.send_email", AsyncMock()):

        result = await submit_onboarding_by_org(
            dograh_org_id=600,
            data={
                "form": {
                    "email": "newbie@startup.com",
                    "businessName": "New Startup",
                }
            },
            db=mock_db
        )

    assert result.dograh_org_id == 600
    assert result.company_name == "New Startup"
    assert result.billing_org_id is None
    assert result.status == "under_review"
    assert added_customers[0].billing_org_id is None
