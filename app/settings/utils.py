def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default

    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def to_list(value: str | None) -> list[str]:
    if not value:
        return []

    return [item.strip() for item in value.split(',') if item.strip()]
