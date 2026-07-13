import os


def feature_enabled() -> bool:
    return bool(os.getenv("FEATURE_ENABLED", ""))
