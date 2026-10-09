import redis.asyncio as redis
from config import settings
import logging
import time

logger = logging.getLogger(__name__)

redis_client = None

# In-memory fallback caches for process-local cooldown and failure tracking
_local_cooldowns: dict[int, float] = {}
_local_failures: dict[int, tuple[int, float]] = {}

async def init_redis():
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            # Test connection
            await redis_client.ping()
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            redis_client = None

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None

async def acquire_auto_recharge_lock(master_id: int, ttl_seconds: int = 120) -> bool:
    """
    Acquire a distributed lock for auto-recharge for the given master_id.
    Fails closed (returns False) if Redis is unavailable or raises an exception,
    preventing concurrent stampedes to payment gateways.
    """
    global redis_client
    if not redis_client:
        try:
            await init_redis()
        except Exception:
            pass
    if not redis_client:
        logger.warning(f"Redis unavailable, auto-recharge lock failing closed for customer {master_id}")
        return False
    try:
        res = await redis_client.set(f"auto_recharge_lock:{master_id}", "1", nx=True, ex=ttl_seconds)
        return bool(res)
    except Exception as e:
        logger.error(f"Redis auto-recharge lock failed: {e}")
        return False

async def release_auto_recharge_lock(master_id: int):
    global redis_client
    if not redis_client:
        return
    try:
        await redis_client.delete(f"auto_recharge_lock:{master_id}")
    except Exception as e:
        logger.error(f"Redis auto-recharge unlock failed: {e}")

async def is_auto_recharge_in_cooldown(master_id: int) -> bool:
    """
    Check if auto-recharge is currently in a cooldown period due to recent payment failure.
    Checks in-memory fallback first, then Redis.
    """
    now = time.time()
    if master_id in _local_cooldowns:
        if now < _local_cooldowns[master_id]:
            return True
        else:
            del _local_cooldowns[master_id]

    global redis_client
    if not redis_client:
        try:
            await init_redis()
        except Exception:
            pass
    if not redis_client:
        return False

    try:
        exists = await redis_client.exists(f"auto_recharge_cooldown:{master_id}")
        return bool(exists)
    except Exception as e:
        logger.error(f"Error checking auto-recharge cooldown in Redis for customer {master_id}: {e}")
        return False

async def record_auto_recharge_failure(master_id: int, base_cooldown_seconds: int = 1800) -> tuple[int, int]:
    """
    Records a payment failure for auto-recharge.
    Increments failure count and sets a cooldown with backoff.
    Returns (failure_count, cooldown_seconds).
    Cooldown progression:
      - 1 failure:  30 minutes (1800s)
      - 2 failures: 2 hours (7200s)
      - 3+ failures: 24 hours (86400s)
    """
    global redis_client
    if not redis_client:
        try:
            await init_redis()
        except Exception:
            pass

    count = 1
    if redis_client:
        try:
            fail_key = f"auto_recharge_failures:{master_id}"
            count = await redis_client.incr(fail_key)
            await redis_client.expire(fail_key, 86400)
        except Exception as e:
            logger.error(f"Error incrementing auto-recharge failure count in Redis: {e}")
            count = _local_failures.get(master_id, (0, 0))[0] + 1
    else:
        count = _local_failures.get(master_id, (0, 0))[0] + 1

    if count == 1:
        cooldown_seconds = base_cooldown_seconds
    elif count == 2:
        cooldown_seconds = 7200
    else:
        cooldown_seconds = 86400

    now = time.time()
    _local_cooldowns[master_id] = now + cooldown_seconds
    _local_failures[master_id] = (count, now)

    if redis_client:
        try:
            cooldown_key = f"auto_recharge_cooldown:{master_id}"
            await redis_client.set(cooldown_key, str(count), ex=cooldown_seconds)
        except Exception as e:
            logger.error(f"Error setting auto-recharge cooldown in Redis: {e}")

    return count, cooldown_seconds

async def clear_auto_recharge_failures(master_id: int):
    """
    Clears failure count and cooldown after a successful recharge or manual settings/card update.
    """
    _local_cooldowns.pop(master_id, None)
    _local_failures.pop(master_id, None)

    global redis_client
    if not redis_client:
        try:
            await init_redis()
        except Exception:
            pass
    if redis_client:
        try:
            await redis_client.delete(
                f"auto_recharge_failures:{master_id}",
                f"auto_recharge_cooldown:{master_id}"
            )
        except Exception as e:
            logger.error(f"Error clearing auto-recharge failure keys in Redis: {e}")

async def get_auto_recharge_failure_count(master_id: int) -> int:
    """Returns current consecutive failure count for auto-recharge."""
    global redis_client
    if redis_client:
        try:
            val = await redis_client.get(f"auto_recharge_failures:{master_id}")
            if val:
                return int(val)
        except Exception as e:
            logger.error(f"Error reading auto-recharge failure count from Redis: {e}")
    return _local_failures.get(master_id, (0, 0))[0]
