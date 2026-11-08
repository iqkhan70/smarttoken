"""Demo file with a fake secret so pack redaction can be tested."""

API_KEY = "sk-test-not-real-abcdefghijklmnopqrstuvwxyz"
AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"

def leaky_header() -> str:
    return "Bearer supersecrettokenvalue"
