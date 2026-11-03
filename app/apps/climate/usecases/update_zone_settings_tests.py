from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import OperationalError

from apps.climate.domain.errors import ZoneNotFoundError
from apps.climate.domain.modes import ZoneKind
from apps.climate.domain.zone_config import ZoneConfig, ZoneConfigRecord
from apps.climate.stubs import ZoneConfigStoreStub
from apps.climate.usecases.update_zone_settings import (
    UpdateZoneSettingsInputDTO,
    UpdateZoneSettingsOutputDTO,
    UpdateZoneSettingsUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
KITCHEN = ZoneConfig(
    name='Кухня',
    heating_enabled=True,
    comfort_floor_setpoint=Decimal('28.0'),
    night_floor_setpoint=Decimal('22.0'),
    boost_floor_setpoint=Decimal('30.0'),
    idle_floor_setpoint=Decimal('18.0'),
    floor_limit_setpoint=Decimal('30.0'),
    comfort_air_target=Decimal('22.0'),
    night_air_target=Decimal('19.5'),
    preheat_lead_minutes=180,
)
BATHROOM = replace(
    KITCHEN,
    name='Санузел 1 этаж',
    comfort_floor_setpoint=Decimal('18.0'),
    night_floor_setpoint=Decimal('18.0'),
)
KITCHEN_EDIT = UpdateZoneSettingsInputDTO(
    zone='kitchen',
    name='Кухня',
    heating_enabled=True,
    comfort_floor_setpoint=Decimal('28.0'),
    night_floor_setpoint=Decimal('22.0'),
    boost_floor_setpoint=Decimal('30.0'),
    idle_floor_setpoint=Decimal('18.0'),
    floor_limit_setpoint=Decimal('30.0'),
    comfort_air_target=Decimal('22.0'),
    night_air_target=Decimal('19.5'),
    preheat_lead_minutes=180,
    now=NOW,
)
GATES = {'live_control', 'air_control_live'}


def _change(field: str, old: str | int | bool, new: str | int | bool, name: str = 'Кухня') -> SettingChange:
    return SettingChange(target='zone', subject='kitchen', name=name, field=field, old=old, new=new)


@pytest.fixture
def f_zones() -> ZoneConfigStoreStub:
    return ZoneConfigStoreStub(
        records=[
            ZoneConfigRecord(
                id=1,
                slug='kitchen',
                kind=ZoneKind.AIR,
                config=KITCHEN,
                live_control=True,
                air_control_live=False,
            ),
            ZoneConfigRecord(
                id=2,
                slug='bathroom-1f',
                kind=ZoneKind.FLOOR,
                config=BATHROOM,
                live_control=True,
                air_control_live=False,
            ),
        ],
    )


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(f_zones: ZoneConfigStoreStub, f_recorder: SettingsRecorderStub) -> UpdateZoneSettingsUseCase:
    return UpdateZoneSettingsUseCase(zones=f_zones, recorder=f_recorder)


@pytest.fixture
def f_locked_use_case(f_zones: ZoneConfigStoreStub) -> UpdateZoneSettingsUseCase:
    return UpdateZoneSettingsUseCase(
        zones=f_zones,
        recorder=SettingsRecorderStub(error=OperationalError('database is locked')),
    )


def test_changed_settings_are_saved_and_journaled_field_by_field(
    f_use_case: UpdateZoneSettingsUseCase,
    f_zones: ZoneConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        replace(KITCHEN_EDIT, comfort_floor_setpoint=Decimal('27.5'), heating_enabled=False, preheat_lead_minutes=150),
    )

    changes = (
        _change('heating_enabled', True, False),
        _change('comfort_floor_setpoint', '28.0', '27.5'),
        _change('preheat_lead_minutes', 180, 150),
    )
    assert (output, f_zones.saved, f_recorder.records) == (
        UpdateZoneSettingsOutputDTO(changes=changes),
        [
            (
                'kitchen',
                replace(
                    KITCHEN,
                    comfort_floor_setpoint=Decimal('27.5'),
                    heating_enabled=False,
                    preheat_lead_minutes=150,
                ),
                ('heating_enabled', 'comfort_floor_setpoint', 'preheat_lead_minutes'),
            ),
        ],
        [(changes, NOW)],
    )


def test_renamed_zone_is_journaled_under_its_new_name(
    f_use_case: UpdateZoneSettingsUseCase,
    f_recorder: SettingsRecorderStub,
) -> None:
    f_use_case.execute(replace(KITCHEN_EDIT, name=' Кухня-столовая ', night_air_target=Decimal('19')))

    assert f_recorder.records == [
        (
            (
                _change('name', 'Кухня', 'Кухня-столовая', name='Кухня-столовая'),
                _change('night_air_target', '19.5', '19.0', name='Кухня-столовая'),
            ),
            NOW,
        ),
    ]


@pytest.mark.parametrize(
    'edit',
    [KITCHEN_EDIT, replace(KITCHEN_EDIT, comfort_floor_setpoint=Decimal('28'), preheat_lead_minutes=180)],
    ids=['as_stored', 'same_number_without_decimals'],
)
def test_settings_as_stored_write_and_journal_nothing(
    f_use_case: UpdateZoneSettingsUseCase,
    f_zones: ZoneConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    edit: UpdateZoneSettingsInputDTO,
) -> None:
    output = f_use_case.execute(edit)

    assert (output, f_zones.saved, f_recorder.records) == (UpdateZoneSettingsOutputDTO(changes=()), [], [])


def test_floor_zone_without_air_targets_keeps_its_own(
    f_use_case: UpdateZoneSettingsUseCase,
    f_zones: ZoneConfigStoreStub,
) -> None:
    f_use_case.execute(
        replace(
            KITCHEN_EDIT,
            zone='bathroom-1f',
            name='Санузел 1 этаж',
            comfort_floor_setpoint=Decimal('18.0'),
            night_floor_setpoint=Decimal('17.0'),
            comfort_air_target=None,
            night_air_target=None,
        ),
    )

    assert f_zones.saved == [
        ('bathroom-1f', replace(BATHROOM, night_floor_setpoint=Decimal('17.0')), ('night_floor_setpoint',)),
    ]


def test_settings_out_of_order_are_refused_and_nothing_is_written(
    f_use_case: UpdateZoneSettingsUseCase,
    f_zones: ZoneConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(
            replace(KITCHEN_EDIT, night_floor_setpoint=Decimal('29.0'), idle_floor_setpoint=Decimal('31.0')),
        )

    assert (raised.value.problems, f_zones.saved, f_recorder.records) == (
        (
            SettingProblem(field='night_floor_setpoint', code='above_comfort'),
            SettingProblem(field='idle_floor_setpoint', code='not_below_limit'),
        ),
        [],
        [],
    )


def test_settings_of_an_unknown_zone_raise(f_use_case: UpdateZoneSettingsUseCase) -> None:
    with pytest.raises(ZoneNotFoundError):
        f_use_case.execute(replace(KITCHEN_EDIT, zone='attic'))


def test_change_the_journal_refuses_is_not_written(
    f_locked_use_case: UpdateZoneSettingsUseCase,
    f_zones: ZoneConfigStoreStub,
) -> None:
    with pytest.raises(OperationalError):
        f_locked_use_case.execute(replace(KITCHEN_EDIT, comfort_floor_setpoint=Decimal('27.5')))

    assert f_zones.saved == []


def test_input_carries_no_gate() -> None:
    assert {field.name for field in fields(UpdateZoneSettingsInputDTO)} & GATES == set()
