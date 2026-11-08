"""Inbound payment webhooks."""

import hashlib
import hmac


def verify_webhook_signature(payload: bytes, header_signature: str, secret: str) -> bool:
    """Check the webhook signature using HMAC-SHA256."""
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_signature)


def handle_webhook(payload: bytes, header_signature: str, secret: str) -> str:
    if not verify_webhook_signature(payload, header_signature, secret):
        return "rejected"
    return "settled"
