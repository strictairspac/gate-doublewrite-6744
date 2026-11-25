"""Fingerprints and canonical serialization for per-page evidence.

The fingerprint binds page order, logical page key, page body and the full
result row together, so a silently swapped or reordered page fails verification.
"""
import hashlib
import json

from src.utils.serial import canonical_json, content_digest


def page_fingerprint(page_no, row):
    body = row.get("读取文本")
    material = {
        "page_no": page_no,
        "page": row.get("页号"),
        "body": body,
        "row": row,
    }
    return hashlib.sha256(canonical_json(material)).hexdigest()


def result_fingerprint(result):
    return content_digest(result)


def encode_row(row):
    return canonical_json(row).decode()


def decode_row(row_json):
    return json.loads(row_json)
