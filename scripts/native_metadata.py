"""Versioned terminal metadata for the bound BMAD dispatch primary SPEC only."""
import hashlib
import re

POLICY = 'bmad-dispatch-status-v1'


def semantic_hash(path):
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    header = re.match(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', text, re.S)
    if header:
        text = re.sub(r'(?m)^status:[^\n]*(?:\n|$)', '', header[1]) + text[header.end():]
        raw = text.encode()
    return hashlib.sha256(raw).hexdigest()
