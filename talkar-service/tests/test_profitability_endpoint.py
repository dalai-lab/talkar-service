import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app
from db.models import TalkarAdmin
from db.session import get_db
from routers.admin import get_profitability, get_current_admin, OPENAI_RATES


@pytest.mark.asyncio
async def test_get_profitability_cost_assumptions_and_rates():
    """Verify get_profitability returns 200 payload with all cost assumptions without NameError."""
    assert "gpt-4o-mini" in OPENAI_RATES
    assert "gpt-4o" in OPENAI_RATES

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[]),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=0),
        )
    )

    for period in ["today", "week", "month", "all"]:
        res = await get_profitability(period=period, db=mock_db, current_admin=MagicMock())
        assert res["period"] == period
        assert "summary" in res
        assert "cost_assumptions" in res
        assumptions = res["cost_assumptions"]
        assert "openai_rates" in assumptions
        assert assumptions["openai_rates"]["gpt-4o-mini"]["input"] == 0.15
        assert assumptions["openai_rates"]["gpt-4o-mini"]["output"] == 0.60


def test_profitability_http_cors_and_status():
    """Verify end-to-end HTTP request with Origin header receives 200 OK and CORS header."""
    client = TestClient(app, raise_server_exceptions=False)
    mock_admin = TalkarAdmin(id=1, email="admin@talkar.in", role="superadmin")

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(
            all=MagicMock(return_value=[]),
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            scalar_one_or_none=MagicMock(return_value=None),
            scalar=MagicMock(return_value=0),
        )
    )

    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        for period in ["today", "week", "month", "all"]:
            resp = client.get(
                f"/admin/profitability?period={period}",
                headers={"Origin": "https://admin.talkar.in"},
            )
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "https://admin.talkar.in"
            data = resp.json()
            assert data["period"] == period
            assert "cost_assumptions" in data
            assert "openai_rates" in data["cost_assumptions"]
    finally:
        app.dependency_overrides.clear()
