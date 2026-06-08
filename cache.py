"""
拾米交易工作室 - 缓存模块
支持 Redis 和内存两种后端，通过 config.py 切换
"""
import time, json, config

# 内存缓存
MEM_CACHE = {}
MEM_TTL = {}  # {key: expires_at}

def _mem_get(key):
    data = MEM_CACHE.get(key)
    expires = MEM_TTL.get(key, 0)
    if data is not None and time.time() < expires:
        return data
    if data is not None:  # expired
        MEM_CACHE.pop(key, None)
        MEM_TTL.pop(key, None)
    return None

def _mem_set(key, data, ttl=None):
    MEM_CACHE[key] = data
    MEM_TTL[key] = time.time() + (ttl or config.CACHE_TTL_DEFAULT)

def _mem_delete(key):
    MEM_CACHE.pop(key, None)
    MEM_TTL.pop(key, None)

def _mem_clear():
    MEM_CACHE.clear()
    MEM_TTL.clear()

# Redis 缓存
_redis_client = None

def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                                     db=config.REDIS_DB, decode_responses=True)
    return _redis_client

def _redis_get(key):
    try:
        raw = _get_redis().get(f"shimi:{key}")
        if raw: return json.loads(raw)
    except Exception: pass
    return None

def _redis_set(key, data, ttl=None):
    try:
        _get_redis().setex(f"shimi:{key}", ttl or config.CACHE_TTL_DEFAULT, json.dumps(data, default=str))
    except Exception: pass

def _redis_delete(key):
    try:
        _get_redis().delete(f"shimi:{key}")
    except Exception: pass

def _redis_clear():
    try:
        for k in _get_redis().scan_iter("shimi:*"):
            _get_redis().delete(k)
    except Exception: pass

# 统一接口
def cache_get(key):
    return _redis_get(key) if config.USE_REDIS else _mem_get(key)

def cache_set(key, data, ttl=None):
    kwargs = {"key": key, "data": data, "ttl": ttl}
    if config.USE_REDIS:
        _redis_set(**kwargs)
    else:
        _mem_set(**kwargs)

def cache_delete(key):
    _redis_delete(key) if config.USE_REDIS else _mem_delete(key)

def cache_clear():
    _redis_clear() if config.USE_REDIS else _mem_clear()

def cache_or_fetch(key, fn, ttl=60):
    cached = cache_get(key)
    if cached is not None:
        return cached
    try:
        data = fn()
        cache_set(key, data, ttl)
        return data
    except Exception as e:
        return {"error": str(e)}
