from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ClimateSensors:
    outdoor_slug: str | None
