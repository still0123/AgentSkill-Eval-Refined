from registry import lookup


def display_name(email: str) -> str:
    result = lookup(email)
    return result if result is not None else "unknown"
