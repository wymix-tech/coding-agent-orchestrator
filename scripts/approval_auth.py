"""Authenticate externally issued approval events. This runtime never holds signing keys."""
import base64
import json


def signing_bytes(event):
    body = {k: v for k, v in event.items() if k != 'signature'}
    return b'orchestrator.approval.v1\x00' + json.dumps(
        body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def authenticate(event, *, keys, audience, channel):
    """Verify a canonical Ed25519 event against project-owned public keys."""
    if not isinstance(event, dict) or not isinstance(keys, dict):
        return False
    key = keys.get(event.get('key_id'))
    if not isinstance(key, dict):
        return False
    if any(not isinstance(event.get(k), str) or not event[k].strip() for k in
           ('id', 'approver', 'subject', 'work_item_id', 'requirement_revision', 'decision')):
        return False
    if (event.get('event') != 'approval' or event.get('audience') != audience
            or event.get('channel') != channel
            or channel not in key.get('channels', [])
            or event['approver'] not in key.get('approvers', [])):
        return False
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return False
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key['public_key'], validate=True))
        public.verify(base64.b64decode(event['signature'], validate=True), signing_bytes(event))
    except (ValueError, TypeError, KeyError, InvalidSignature, UnsupportedAlgorithm):
        return False
    return True
