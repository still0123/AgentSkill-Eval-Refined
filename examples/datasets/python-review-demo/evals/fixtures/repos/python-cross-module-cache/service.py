from cache import STORE


def read_user(user_id: int) -> str | None:
    return STORE.get(str(user_id))
