from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

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
from apps.core.stubs import SettingsRecorderStub, TransactionStub

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
DEFAULTS = ClimateConfig(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    air_min_band=Decimal('0.20'),
    learned_mornings_needed=3,
    preheat_lead_cap_minutes=240,
    second_floor_guard_threshold=Decimal('19.0'),
    second_floor_guard_hysteresis=Decimal('0.30'),
)
EDIT = UpdateClimateSettingsInputDTO(
    air_start_offset=Decimal('0.30'),
    initial_early_stop_offset=Decimal('0.30'),
    air_min_band=Decimal('0.20'),
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
    return UpdateClimateSettingsUseCase(
        climate_settings=f_climate_settings,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=None),
    )


@pytest.fixture
def f_use_case_after_the_start_offset_was_narrowed_meanwhile(
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateClimateSettingsUseCase:
    narrowed = replace(DEFAULTS, air_start_offset=Decimal('0.20'))
    return UpdateClimateSettingsUseCase(
        climate_settings=f_climate_settings,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_climate_settings.save_config, narrowed, ('air_start_offset',))),
    )


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


def test_changed_min_band_is_saved_and_journaled_like_the_other_settings(
    f_use_case: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(replace(EDIT, air_min_band=Decimal('0.25')))

    changes = (
        SettingChange(
            target='climate_settings',
            subject='',
            name='',
            field='air_min_band',
            old='0.20',
            new='0.25',
        ),
    )
    assert (output, f_climate_settings.saved, f_recorder.records) == (
        UpdateClimateSettingsOutputDTO(changes=changes),
        [(replace(DEFAULTS, air_min_band=Decimal('0.25')), ('air_min_band',))],
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


@pytest.mark.parametrize(
    'edit',
    [replace(EDIT, air_start_offset=Decimal('0.15')), replace(EDIT, air_min_band=Decimal('0.40'))],
    ids=['start_offset_narrowed', 'band_widened'],
)
def test_start_offset_below_the_min_band_is_refused_at_the_start_offset(
    f_use_case: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    edit: UpdateClimateSettingsInputDTO,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(edit)

    assert (raised.value.problems, f_climate_settings.saved, f_recorder.records) == (
        (SettingProblem(field='air_start_offset', code='below_min_band'),),
        [],
        [],
    )


def test_input_carries_no_guard_gate() -> None:
    assert 'second_floor_guard_live' not in {field.name for field in fields(UpdateClimateSettingsInputDTO)}


def test_band_saved_after_another_save_narrowed_the_start_offset_is_written_with_its_own_start_offset(
    f_use_case_after_the_start_offset_was_narrowed_meanwhile: UpdateClimateSettingsUseCase,
    f_climate_settings: ClimateConfigStoreStub,
) -> None:
    output = f_use_case_after_the_start_offset_was_narrowed_meanwhile.execute(
        replace(EDIT, air_min_band=Decimal('0.30')),
    )

    widened = replace(DEFAULTS, air_min_band=Decimal('0.30'))
    assert (output.changes, f_climate_settings.saved[-1], f_climate_settings.config_record_for_update().config) == (
        (
            SettingChange(
                target='climate_settings',
                subject='',
                name='',
                field='air_start_offset',
                old='0.20',
                new='0.30',
            ),
            SettingChange(target='climate_settings', subject='', name='', field='air_min_band', old='0.20', new='0.30'),
        ),
        (widened, ('air_start_offset', 'air_min_band')),
        widened,
    )
