import hashlib

_SAFE_FILENAME_MAX_CHARS = 96


def sanitize_filename(name: str) -> str:
    """把标题清洗成有界安全文件名，空标题回退 untitled。"""
    cleaned = "".join(
        c for c in name if c not in '\\/:*?"<>|' and c.isprintable()
    ).strip()[:_SAFE_FILENAME_MAX_CHARS].rstrip(" .")
    return cleaned or "untitled"


def path_hash_suffix(path: str) -> str:
    """任意稳定标识 → 8 位短哈希，避免同名产物互相覆盖。"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:8]


def creation_output_path(title: str, activity_id: str) -> str:
    """创作标题 + activity id → 唯一且有界的 workspace 相对路径。"""
    stem = sanitize_filename(title)
    return f"creations/{stem}-{path_hash_suffix(activity_id)}.md"
