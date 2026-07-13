STORE: dict[str, str] = {}


def write_user(user_id: int, value: str) -> None:
    STORE[f"user:{user_id}"] = value
