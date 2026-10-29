from datetime import datetime, timedelta

MIN_WRITE_INTERVAL = timedelta(minutes=30)


def write_allowed(
    last_command_at: datetime | None,
    now: datetime,
    min_interval: timedelta = MIN_WRITE_INTERVAL,
) -> bool:
    return last_command_at is None or last_command_at > now or now - last_command_at >= min_interval
