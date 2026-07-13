def page_at(items: list[str], page: int, page_size: int) -> list[str]:
    page_count = (len(items) + page_size - 1) // page_size
    if page < 0 or page > page_count:
        raise IndexError("page out of range")
    start = page * page_size
    return items[start : start + page_size]
