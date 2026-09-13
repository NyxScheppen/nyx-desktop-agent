import hashlib


def sanitize_filename(name: str) -> str:
    """把标题清洗成安全文件名，空标题回退 untitled。"""
    cleaned = "".join(
        c for c in name if c not in '\\/:*?"<>|' and c.isprintable()
    ).strip()
    return cleaned or "untitled"


def path_hash_suffix(path: str) -> str:
    """读物绝对路径 → 8 位短哈希，避免同名书落盘互相覆盖。"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:8]
