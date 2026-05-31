"""
拾米交易工作室 - 缓存模块
支持 Redis 和内存两种后端，通过 config.py 切换
"""
import time
import json

import config

# ─── 内存缓存（默认） ─────────────────────────────────
MEM_CACHE = {}
MEM_UPDATED = {}


def _mem_get(key):
    """内存缓存读取"""
    now = time.time()
    data = MEM_CACHE.get(key)
    updated = MEM_UPDATED.get(key, 0)
    if data is not None and (now - updated) < config.CACHE_TTL_DEFAULT:
        return data
    return None


def _mem_set(key, data, ttl=None):
    """内存缓存写入"""
    MEM_CACHE[key] = data
    MEM_UPDATED[key] = time.time()


def _mem_delete(key):
    """内存缓存删除"""
    MEM_CACHE.pop(key, None)
    MEM_UPDATED.pop(key, None)


def _mem_clear():
    """清空内存缓存"""
    MEM_CACHE.clear()
    MEM_UPDATED.clear()


# ─── Redis 缓存 ─────────────────────────────────────
_redis_client = None


def _get_redis():
    """获取 Redis 连接（懒加载）"""
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            decode_responses=True,
        )
    return _redis_client


def _redis_get(key):
    """Redis 缓存读取"""
    try:
        r = _get_redis()
        raw = r.get(f"shimi:{key}")
        if raw:
            return json.loads(raw)
    except:
        pass
    return None


def _redis_set(key, data, ttl=None):
    """Redis 缓存写入"""
    try:
        r = _get_redis()
        r.setex(f"shimi:{key}", ttl or config.CACHE_TTL_DEFAULT, json.dumps(data, default=str))
    except:
        pass


def _redis_delete(key):
    """Redis 缓存删除"""
    try:
        r = _get_redis()
        r.delete(f"shimi:{key}")
    except:
        pass


def _redis_clear():
    """清空 Redis 缓存"""
    try:
        r = _get_redis()
        for k in r.scan_iter("shimi:*"):
            r.delete(k)
    except:
        pass


# ─── 统一接口 ───────────────────────────────────────

def cache_get(key):
    """缓存读取"""
    if config.USE_REDIS:
        return _redis_get(key)
    return _mem_get(key)


def cache_set(key, data, ttl=None):
    """缓存写入"""
    if config.USE_REDIS:
        _redis_set(key, data, ttl)
    else:
        _mem_set(key, data, ttl)


def cache_delete(key):
    """缓存删除"""
    if config.USE_REDIS:
        _redis_delete(key)
    else:
        _mem_delete(key)


def cache_clear():
    """清空缓存"""
    if config.USE_REDIS:
        _redis_clear()
    else:
        _mem_clear()


def cache_or_fetch(key, fn, ttl=60):
    """缓存取或重新计算（替代 backend.py 中的同名函数）

    Args:
        key: 缓存键
        fn: 取数函数（数据过期时调用）
        ttl: 缓存 TTL（秒）

    Returns:
        缓存数据（dict/list 等 JSON 可序列化对象）
    """
    # 先读缓存
    cached = cache_get(key)
    if cached is not None:
        return cached

    # 缓存未命中 → 调用 fn
    try:
        data = fn()
        cache_set(key, data, ttl)
        return data
    except Exception as e:
        return {"error": str(e)}
