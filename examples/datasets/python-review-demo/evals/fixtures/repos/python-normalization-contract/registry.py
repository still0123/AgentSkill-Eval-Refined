USERS = {"alice@example.com": "Alice"}


def lookup(email: str) -> str | None:
    return USERS.get(email)
