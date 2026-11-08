from src.payments.webhook import verify_webhook_signature


def test_signature_rejects_bad_header() -> None:
    assert verify_webhook_signature(b"body", "nope", "secret") is False
