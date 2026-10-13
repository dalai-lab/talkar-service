import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add talkar-service to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import Customer, Wallet
from services import redis_client
import services.billing_service as billing_service


@pytest.mark.asyncio
async def test_acquire_auto_recharge_lock_generates_unique_owner_token():
    """acquire_auto_recharge_lock generates a unique token, sets NX with TTL, and returns token."""
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)

    with patch.object(redis_client, "redis_client", mock_redis):
        token = await redis_client.acquire_auto_recharge_lock(master_id=42, ttl_seconds=60)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 10  # UUID string
        mock_redis.set.assert_awaited_once_with(
            "auto_recharge_lock:42", token, nx=True, ex=60
        )


@pytest.mark.asyncio
async def test_acquire_auto_recharge_lock_returns_none_when_already_held():
    """acquire_auto_recharge_lock returns None when Redis SET NX returns False (lock held)."""
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=False)

    with patch.object(redis_client, "redis_client", mock_redis):
        token = await redis_client.acquire_auto_recharge_lock(master_id=42, ttl_seconds=120)

        assert token is None
        mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_auto_recharge_lock_executes_atomic_lua_with_token():
    """release_auto_recharge_lock uses Lua script to ensure only the lock owner can release."""
    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(return_value=1)

    with patch.object(redis_client, "redis_client", mock_redis):
        released = await redis_client.release_auto_recharge_lock(master_id=42, token="token-worker-a")

        assert released is True
        mock_redis.eval.assert_awaited_once_with(
            redis_client.RELEASE_LOCK_LUA,
            1,
            "auto_recharge_lock:42",
            "token-worker-a",
        )


@pytest.mark.asyncio
async def test_release_auto_recharge_lock_prevents_stale_worker_releasing_subsequent_lock():
    """When a worker delayed beyond TTL attempts release, Lua script returns 0 and does not delete key."""
    # Simulate Redis state: key currently contains "token-worker-b"
    store = {"auto_recharge_lock:42": "token-worker-b"}

    async def mock_eval(script, numkeys, key, token):
        # Simulate RELEASE_LOCK_LUA logic
        if store.get(key) == token:
            store.pop(key, None)
            return 1
        return 0

    mock_redis = MagicMock()
    mock_redis.eval = AsyncMock(side_effect=mock_eval)

    with patch.object(redis_client, "redis_client", mock_redis):
        # Worker A tries to release using stale token "token-worker-a"
        res_a = await redis_client.release_auto_recharge_lock(master_id=42, token="token-worker-a")

        assert res_a is False
        # Worker B's lock remains intact in Redis!
        assert store.get("auto_recharge_lock:42") == "token-worker-b"

        # Worker B then releases using matching token "token-worker-b"
        res_b = await redis_client.release_auto_recharge_lock(master_id=42, token="token-worker-b")
        assert res_b is True
        assert "auto_recharge_lock:42" not in store


@pytest.mark.asyncio
async def test_check_and_trigger_auto_recharge_passes_owner_token_to_release():
    """check_and_trigger_auto_recharge acquires owner token and passes it to release_auto_recharge_lock."""
    mock_db = MagicMock()

    customer = Customer(id=10, dograh_org_id=100, status="active")
    wallet = Wallet(
        id=1,
        customer_id=10,
        balance_paise=10000,
        auto_recharge_enabled=True,
        auto_recharge_threshold_paise=50000,
        auto_recharge_amount_paise=100000,
        razorpay_payment_method_id="token_123",
        razorpay_customer_id="cust_123",
    )

    res_cust = MagicMock()
    res_cust.scalar_one_or_none.return_value = customer
    mock_db.execute = AsyncMock(return_value=res_cust)

    mock_acquire = AsyncMock(return_value="owner-token-xyz")
    mock_release = AsyncMock(return_value=True)

    with (
        patch("services.billing_service.get_billing_wallet", AsyncMock(return_value=(wallet, 10))),
        patch("services.redis_client.is_auto_recharge_in_cooldown", AsyncMock(return_value=False)),
        patch("services.redis_client.acquire_auto_recharge_lock", mock_acquire),
        patch("services.redis_client.release_auto_recharge_lock", mock_release),
        patch("services.razorpay_client.charge_saved_card", AsyncMock(return_value={"status": "captured", "id": "order_123"})),
        patch("services.billing_service.credit_wallet", AsyncMock()),
        patch("services.billing_service.sync_wallet_block_policy", AsyncMock()),
        patch("services.redis_client.clear_auto_recharge_failures", AsyncMock()),
    ):
        await billing_service.check_and_trigger_auto_recharge(mock_db, 10)

        mock_acquire.assert_awaited_once_with(10, ttl_seconds=120)
        # release_auto_recharge_lock must be called with token="owner-token-xyz"
        mock_release.assert_awaited_once_with(10, token="owner-token-xyz")
