import redis.asyncio as redis
from config import settings
import logging

logger = logging.getLogger(__name__)

redis_client = None

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

async def get_active_calls(master_id: int) -> int:
    global redis_client
    if not redis_client:
        return 0
    try:
        val = await redis_client.get(f"billing_group_active:{master_id}")
        return max(0, int(val)) if val else 0
    except Exception as e:
        logger.error(f"Redis GET failed for billing_group_active:{master_id}: {e}")
        return 0

async def increment_active_calls(master_id: int, ttl_seconds: int):
    global redis_client
    if not redis_client:
        return
    try:
        key = f"billing_group_active:{master_id}"
        # INCR and fetch current TTL atomically.
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        results = await pipe.execute()
        new_val = results[0]
        current_ttl = results[1]  # -1 = no expiry, -2 = key missing, >=0 = seconds left

        # Always ensure the key's TTL is at least as long as this call's max duration.
        # Without this, a key created by an earlier call can expire mid-campaign,
        # the next INCR creates a fresh key with no TTL, and it stays forever.
        # current_ttl < ttl_seconds covers the case where an older call set a shorter TTL.
        if current_ttl < 0 or current_ttl < ttl_seconds:
            await redis_client.expire(key, ttl_seconds + 60)
    except Exception as e:
        logger.error(f"Redis INCR failed for billing_group_active:{master_id}: {e}")

# Lua script: atomically decrement but never go below 0.
# Plain DECR can drift negative when orphaned deductions arrive for calls
# that were never quota-checked (old cron runs, crash-recovery, etc.).
# A floored counter guarantees get_active_calls() always reads truthfully.
_LUA_DECR_FLOOR_ZERO = """
local val = tonumber(redis.call('get', KEYS[1]) or '0')
if val <= 0 then
    redis.call('set', KEYS[1], '0')
    return 0
end
return redis.call('decr', KEYS[1])
"""

async def decrement_active_calls(master_id: int):
    global redis_client
    if not redis_client:
        return
    try:
        key = f"billing_group_active:{master_id}"
        await redis_client.eval(_LUA_DECR_FLOOR_ZERO, 1, key)
    except Exception as e:
        logger.error(f"Redis DECR failed for billing_group_active:{master_id}: {e}")

async def acquire_auto_recharge_lock(master_id: int, ttl_seconds: int = 120) -> bool:
    global redis_client
    if not redis_client:
        return True
    try:
        res = await redis_client.set(f"auto_recharge_lock:{master_id}", "1", nx=True, ex=ttl_seconds)
        return bool(res)
    except Exception as e:
        logger.error(f"Redis auto-recharge lock failed: {e}")
        return True

async def release_auto_recharge_lock(master_id: int):
    global redis_client
    if not redis_client:
        return
    try:
        await redis_client.delete(f"auto_recharge_lock:{master_id}")
    except Exception as e:
        logger.error(f"Redis auto-recharge unlock failed: {e}")
