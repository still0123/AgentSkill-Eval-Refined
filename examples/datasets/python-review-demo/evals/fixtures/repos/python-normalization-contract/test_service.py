from service import display_name


def test_lookup_is_case_insensitive() -> None:
    assert display_name("ALICE@EXAMPLE.COM") == "Alice"
