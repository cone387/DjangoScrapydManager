import hashlib


def get_md5(string: str):
    m = hashlib.md5()
    m.update(string.encode('utf-8'))
    return m.hexdigest()
