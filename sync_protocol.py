"""Canonical content receipts shared by the local bridge and cloud hub."""
import hashlib
import json


def content_fingerprint(chapters, settings):
    records = []
    for kind, documents in (("chapters", chapters), ("settings", settings)):
        for document in documents:
            digest = hashlib.sha256(document["content"].encode("utf-8")).hexdigest()
            records.append((kind, document["filename"], digest))
    payload = json.dumps(sorted(records), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
