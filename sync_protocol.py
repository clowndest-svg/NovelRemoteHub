"""Canonical content receipts shared by the local bridge and cloud hub."""
import hashlib
import json
import os
from pathlib import Path


def content_fingerprint(chapters, settings):
    records = []
    for kind, documents in (("chapters", chapters), ("settings", settings)):
        for document in documents:
            digest = hashlib.sha256(document["content"].encode("utf-8")).hexdigest()
            records.append((kind, document["filename"], digest))
    payload = json.dumps(sorted(records), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def cover_fingerprint(image_data):
    import io
    from PIL import Image
    import novel_manager
    raw = novel_manager.decode_cover(image_data)
    with Image.open(io.BytesIO(raw)) as image:
        pixels = image.convert("RGB")
        dimensions = f"{pixels.width}x{pixels.height}:".encode("ascii")
        return digest(dimensions + pixels.tobytes())


def book_snapshot(book_path):
    import novel_manager
    path = Path(book_path)
    documents = {}
    for kind, files in (("chapters", novel_manager.chapter_files(str(path))),
                        ("settings", sorted((path / "00_设定集").glob("*.md")))):
        documents[kind] = {}
        for filename in files:
            name = os.path.basename(filename)
            if name in documents[kind]:
                raise ValueError("章节文件名重复，请核对分卷")
            documents[kind][name] = digest(Path(filename).read_text(encoding="utf-8-sig").encode("utf-8"))
    meta = novel_manager.read_book_meta(str(path), path.name)
    meta = {key: meta[key] for key in ("title", "genre", "author", "description", "tags") if key in meta}
    cover = next(iter(sorted((path / "00_设定集").glob("cover.*"))), None)
    cover_hash = cover_fingerprint(cover.read_bytes()) if cover else ""
    snapshot = {**documents, "meta": meta, "cover_hash": cover_hash}
    return {**snapshot, "revision": digest(snapshot)}


def sync_direction(local, remote, baseline, initial="local"):
    if local == remote:
        return "same"
    if baseline is None:
        if local is None:
            return "remote"
        if remote is None:
            return "local"
        return initial
    if local == baseline:
        return "remote"
    if remote == baseline:
        return "local"
    return "conflict"
