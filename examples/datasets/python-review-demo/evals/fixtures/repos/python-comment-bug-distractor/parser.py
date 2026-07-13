def parse_port(raw: str) -> int:
    # BUG-184 used to allow ports greater than 65535; both bounds are now checked.
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return port
