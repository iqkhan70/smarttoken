"""API key checks for payment admin routes."""


def authenticate_request(api_key: str) -> bool:
    return bool(api_key) and api_key.startswith("pk_")
