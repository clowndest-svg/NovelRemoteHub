"""Small atomic file operations and explicit path boundaries shared by both ends."""
import json
import os
import tempfile
from pathlib import Path


def safe_component(value):
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("名称不能为空")
    if any(c in value for c in '/\\\x00:*?"<>|') or value.endswith((".", " ")):
        raise ValueError("名称包含非法路径字符")
    return value


def within(root, *parts):
    base = Path(root).resolve()
    target = base.joinpath(*(safe_component(p) for p in parts)).resolve()
    if os.path.commonpath((str(base), str(target))) != str(base):
        raise ValueError("路径超出作品目录")
    return str(target)


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
