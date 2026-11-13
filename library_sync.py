"""Reconcile acknowledged local/cloud revisions without resurrecting removed files."""
import os
import uuid
from pathlib import Path
from storage import atomic_write, safe_component, within
from urllib.parse import urlencode
from sync_protocol import book_snapshot, sync_direction, digest, cover_fingerprint
import novel_manager


def archive(path, root, category):
    base = Path(root).resolve()
    path = path.resolve()
    target = (base / category / uuid.uuid4().hex / path.name).resolve()
    if not path.is_relative_to(base) or not target.is_relative_to(base):
        raise ValueError("归档路径超出作品目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)


def reconcile(root, manifest, state, get):
    """Pull remote-only edits. Push-side optimistic revision checks close the request race."""
    root = Path(root)
    for book_id in manifest.get("deleted", {}):
        path = Path(within(root, book_id))
        if path.exists():
            archive(path, root, ".trash")
        state.pop(book_id, None)
    for book_id, remote in manifest.get("books", {}).items():
        path = Path(within(root, book_id))
        path.mkdir(parents=True, exist_ok=True)
        local = book_snapshot(path)
        previous = state.get(book_id, {})
        for kind, directory in (("chapters", "01_正文"), ("settings", "00_设定集")):
            for name in set(local[kind]) | set(remote[kind]):
                safe_component(name)
                prior = previous.get(kind, {}).get(name)
                direction = sync_direction(local[kind].get(name), remote[kind].get(name), prior, initial="conflict")
                if direction == "conflict":
                    raise ValueError(f"《{book_id}》{name} 本地与云端均已修改，已保留两端原稿，请核对后同步")
                if direction != "remote":
                    continue
                candidates = (novel_manager.chapter_files(str(path)) if kind == "chapters"
                              else (path / directory).glob("*.md"))
                target = next((Path(p) for p in candidates if Path(p).name == name), None)
                if name not in remote[kind]:
                    if target:
                        if digest(target.read_text(encoding="utf-8-sig").encode("utf-8")) != local[kind].get(name):
                            raise ValueError("本地文档在同步期间修改，已保留原稿，请重试")
                        archive(target, path, ".sync-archive")
                    continue
                response = get("sync_document?" + urlencode(
                    {"book": book_id, "kind": kind, "file": name}))
                if not response or "content" not in response:
                    raise RuntimeError(f"无法下载《{book_id}》{name}，稍后重试")
                content = response["content"]
                if digest(content.encode("utf-8")) != remote[kind][name]:
                    raise RuntimeError("云端文档读取期间发生变化，稍后重新同步")
                if target is None:
                    target = path / directory
                    if kind == "chapters":
                        target /= "第一卷_人界篇"
                    target /= name
                actual = digest(target.read_text(encoding="utf-8-sig").encode("utf-8")) if target.exists() else None
                if actual != local[kind].get(name):
                    raise ValueError("本地文档在下载期间修改，已保留原稿，请重试")
                atomic_write(target, content)
        direction = sync_direction(local["meta"], remote["meta"], previous.get("meta"), initial="remote")
        if direction == "conflict":
            raise ValueError(f"《{book_id}》资料在两端同时修改，请核对后同步")
        if direction == "remote":
            if book_snapshot(path)["meta"] not in (local["meta"], remote["meta"]):
                raise ValueError("本地资料在同步期间修改，请重试")
            novel_manager.save_book_meta(book_id, remote["meta"], str(root), rename=False)
        direction = sync_direction(local["cover_hash"] or None, remote["cover_hash"] or None,
                                   previous.get("cover_hash") or None, initial="remote")
        if direction == "conflict":
            raise ValueError(f"《{book_id}》封面在两端同时修改，请核对后同步")
        if direction == "remote" and remote["cover_hash"]:
            response = get("sync_document?" + urlencode(
                {"book": book_id, "kind": "cover"}))
            if not response or not response.get("cover_base64"):
                raise RuntimeError("封面下载失败，稍后重试")
            raw = novel_manager.decode_cover(response["cover_base64"])
            if cover_fingerprint(raw) != remote["cover_hash"]:
                raise RuntimeError("封面读取期间发生变化，稍后重试")
            if book_snapshot(path)["cover_hash"] != local["cover_hash"]:
                raise ValueError("本地封面在同步期间修改，请重试")
            for cover in (path / "00_设定集").glob("cover.*"):
                archive(cover, path, ".sync-archive")
            novel_manager.save_book_cover(book_id, raw, root_dir=str(root))
        elif direction == "remote" and not remote["cover_hash"]:
            for cover in (path / "00_设定集").glob("cover.*"):
                archive(cover, path, ".sync-archive")
    return state
