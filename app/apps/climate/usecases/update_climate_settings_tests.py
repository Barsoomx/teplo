from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.climate.domain.climate_config import ClimateConfig, ClimateConfigRecord
from apps.climate.stubs import ClimateConfigStoreStub
from apps.climate.usecases.update_climate_settings import (
    UpdateClimateSettingsInputDTO,
    UpdateClimateSettingsOutputDTO,
    UpdateClimateSettingsUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
DEFAULTS = ClimateConfig(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
    second_floor_guard_threshold=Decimal('19.0'),
    second_floor_guard_hysteresis=Decimal('0.30'),
)
EDIT = UpdateClimateSettingsInputDTO(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
    second_floor_guard_threshold=Decimal('19.0'),
    second_floor_guard_hysteresis=Decimal('0.30'),
    now=NOW,
)


@pytest.fixture
def f_climate_settings() -> ClimateConfigStoreStub:
    return ClimateConfigStoreStub(record=ClimateConfigRecord(id=1, config=DEFAULTS, second_floor_guard_live=True))


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateClimateSettingsUseCase:
    return UpdateClimateSettingsUseCase(climate_settings=f_climate_settings, recorder=f_recorder)


def test_changed_climate_settings_are_saved_and_journaled(
    f_use_case: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        replace(EDIT, air_start_offset=Decimal('0.4'), second_floor_guard_threshold=Decimal('18.5')),
    )

    changes = (
        SettingChange(
            target='climate_settings',
            subject='',
            name='',
            field='air_start_offset',
            old='0.30',
            new='0.40',
        ),
        SettingChange(
            target='climate_settings',
            subject='',
            name='',
            field='second_floor_guard_threshold',
            old='19.0',
            new='18.5',
        ),
    )
    assert (output, f_climate_settings.saved, f_recorder.records) == (
        UpdateClimateSettingsOutputDTO(changes=changes),
        [
            (
                replace(DEFAULTS, air_start_offset=Decimal('0.40'), second_floor_guard_threshold=Decimal('18.5')),
                ('air_start_offset', 'second_floor_guard_threshold'),
            ),
        ],
        [(changes, NOW)],
    )


def test_climate_settings_as_stored_write_and_journal_nothing(
    f_use_case: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(EDIT)

    assert (output, f_climate_settings.saved, f_recorder.records) == (
        UpdateClimateSettingsOutputDTO(changes=()),
        [],
        [],
    )


def test_climate_settings_out_of_range_are_refused(
    f_use_case: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(replace(EDIT, initial_early_stop_offset=Decimal('1.6'), preheat_lead_cap_minutes=500))

    assert (raised.value.problems, f_climate_settings.saved) == (
        (
            SettingProblem(field='initial_early_stop_offset', code='out_of_range'),
            SettingProblem(field='preheat_lead_cap_minutes', code='out_of_range'),
        ),
        [],
    )


def test_input_carries_no_guard_gate() -> None:
    assert 'second_floor_guard_live' not in {field.name for field in fields(UpdateClimateSettingsInputDTO)}
