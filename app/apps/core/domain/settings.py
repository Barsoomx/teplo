from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

SettingValue = str | int | bool | tuple[str, ...] | None


@dataclass(frozen=True, kw_only=True)
class SettingChange:
    target: str
    subject: str
    name: str
    field: str
    old: SettingValue
    new: SettingValue

    def payload(self) -> dict[str, object]:
        return {
            'object': self.target,
            'name': self.name,
            'field': self.field,
            'old': _payload_value(self.old),
            'new': _payload_value(self.new),
        }


@dataclass(frozen=True, kw_only=True)
class SettingProblem:
    field: str
    code: str
    subject: str = ''


def setting_value(value: object) -> SettingValue:
    if value is None or isinstance(value, bool | int):
        return value

    if isinstance(value, Decimal | str):
        return str(value)

    if isinstance(value, tuple):
        return tuple(str(item) for item in value)

    return str(value)


def setting_changes(
    *,
    target: str,
    subject: str,
    name: str,
    old: Mapping[str, object],
    new: Mapping[str, object],
) -> tuple[SettingChange, ...]:
    return tuple(
        SettingChange(
            target=target,
            subject=subject,
            name=name,
            field=field,
            old=setting_value(value),
            new=setting_value(new[field]),
        )
        for field, value in old.items()
        if value != new[field]
    )


def _payload_value(value: SettingValue) -> object:
    return list(value) if isinstance(value, tuple) else value
