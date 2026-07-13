from collections.abc import Callable


def call_with_retry(operation: Callable[[], str], max_attempts: int) -> str:
    attempts = 0
    while attempts <= max_attempts:
        attempts += 1
        try:
            return operation()
        except TimeoutError:
            if attempts > max_attempts:
                raise
    raise RuntimeError("unreachable")
