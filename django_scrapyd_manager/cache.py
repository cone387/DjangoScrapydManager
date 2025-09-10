import time
from functools import wraps
from django.core.cache import cache
from django.http import HttpRequest
from django.contrib.admin import ModelAdmin

_global_cache = {}
_sentinel = object()

_exclude_key_type = (ModelAdmin, HttpRequest)


def get_fun_cacheable_args_and(*args, **kwargs):
    key_args = []
    key_kwargs = {}
    for arg in args:
        if not isinstance(arg, _exclude_key_type):
            key_args.append(arg)
    for k, v in kwargs.items():
        if not isinstance(v, _exclude_key_type):
            key_kwargs[k] = v
    return key_args, key_kwargs


# 内存 TTL cache
def ttl_cache(ttl: int = 60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key_args, key_kwargs = get_fun_cacheable_args_and(*args, **kwargs)
            key = f"ttl_cache:{func.__name__}:args={key_args}:kwargs={key_kwargs}"

            now = time.time()
            if key in _global_cache:
                result, expire_at = _global_cache[key]
                if expire_at > now:
                    return result
            result = func(*args, **kwargs)
            _global_cache[key] = (result, now + ttl)
            return result
        return wrapper
    return decorator


def django_ttl_cache(ttl=10, prefix="ttl_cache"):
    import hashlib, pickle
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key_args, key_kwargs = get_fun_cacheable_args_and(*args, **kwargs)
            raw_key = (func.__name__, key_args, key_kwargs)
            key = f"{prefix}:{hashlib.md5(pickle.dumps(raw_key)).hexdigest()}"

            result = cache.get(key, _sentinel)
            if result is not _sentinel:
                return result
            result = func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator
