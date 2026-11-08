"""HTTP entrypoint for the payments fixture."""

from src.payments.webhook import handle_webhook


def main() -> None:
    handle_webhook(b"{}", "sig", "secret")


if __name__ == "__main__":
    main()
