from typing import Optional


def find_profile(user_id: str) -> Optional[dict[str, str]]:
    return None if user_id == "missing" else {"display_name": user_id.title()}


def greeting(user_id: str) -> str:
    profile = find_profile(user_id)
    if profile is None:
        return "Hello guest"
    return f"Hello {profile['display_name']}"
