from datetime import timedelta


def elapsed(since: float | None, at: float, interval: timedelta) -> bool:
    return since is None or at - since >= interval.total_seconds()


def held(since: float | None, at: float, hold: timedelta) -> bool:
    return since is not None and at - since >= hold.total_seconds()


def held_since(since: float | None, holds: bool, at: float) -> float | None:
    if not holds:
        return None

    return at if since is None else since
