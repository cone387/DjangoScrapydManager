import time
from functools import wraps


_global_cache = {}


def ttl_cache(ttl: int = 60):
    """
    公共 TTL 缓存装饰器
    - 所有被装饰的函数共享同一个 cache 池
    - cache key = (函数名, 参数)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 以函数名+参数为 key，避免冲突
            key = (func.__name__, args, frozenset(kwargs.items()))
            now = time.time()

            # 命中缓存且未过期
            if key in _global_cache:
                result, expire_at = _global_cache[key]
                if expire_at > now:
                    return result

            # 重新计算并写入缓存
            result = func(*args, **kwargs)
            _global_cache[key] = (result, now + ttl)
            return result

        return wrapper
    return decorator
