from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub
from apps.power.domain.load_config import LoadSettingsConfig, LoadSettingsRecord
from apps.power.stubs import LoadSettingsConfigStoreStub
from apps.power.usecases.update_load_settings import (
    UpdateLoadSettingsInputDTO,
    UpdateLoadSettingsOutputDTO,
    UpdateLoadSettingsUseCase,
)

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
DEFAULTS = LoadSettingsConfig(
    start_ceiling_amps=Decimal('20.0'),
    shed_amps=Decimal('24.0'),
    shed_hold_seconds=3,
    restore_amps=Decimal('18.0'),
    restore_hold_seconds=120,
    start_interval_seconds=30,
    meter_lost_after_seconds=60,
    default_load_amps=Decimal('6.0'),
)
EDIT = UpdateLoadSettingsInputDTO(
    start_ceiling_amps=Decimal('20.0'),
    shed_amps=Decimal('24.0'),
    shed_hold_seconds=3,
    restore_amps=Decimal('18.0'),
    restore_hold_seconds=120,
    start_interval_seconds=30,
    meter_lost_after_seconds=60,
    default_load_amps=Decimal('6.0'),
    now=NOW,
)


def _change(field: str, old: str | int, new: str | int) -> SettingChange:
    return SettingChange(target='load_settings', subject='', name='', field=field, old=old, new=new)


@pytest.fixture
def f_load_settings() -> LoadSettingsConfigStoreStub:
    return LoadSettingsConfigStoreStub(record=LoadSettingsRecord(id=1, config=DEFAULTS, load_management_live=True))


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(
    f_load_settings: LoadSettingsConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateLoadSettingsUseCase:
    return UpdateLoadSettingsUseCase(
        load_settings=f_load_settings,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=None),
    )


@pytest.fixture
def f_use_case_after_the_start_ceiling_was_raised_meanwhile(
    f_load_settings: LoadSettingsConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateLoadSettingsUseCase:
    raised = replace(DEFAULTS, start_ceiling_amps=Decimal('22.0'))
    return UpdateLoadSettingsUseCase(
        load_settings=f_load_settings,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_load_settings.save_config, raised, ('start_ceiling_amps',))),
    )


def test_raised_thresholds_are_saved_and_journaled(
    f_use_case: UpdateLoadSettingsUseCase,
    f_load_settings: LoadSettingsConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        replace(EDIT, start_ceiling_amps=Decimal('21'), shed_amps=Decimal('25.0'), shed_hold_seconds=5),
    )

    changes = (
        _change('start_ceiling_amps', '20.0', '21.0'),
        _change('shed_amps', '24.0', '25.0'),
        _change('shed_hold_seconds', 3, 5),
    )
    assert (output, f_load_settings.saved, f_recorder.records) == (
        UpdateLoadSettingsOutputDTO(changes=changes),
        [
            (
                replace(DEFAULTS, start_ceiling_amps=Decimal('21.0'), shed_amps=Decimal('25.0'), shed_hold_seconds=5),
                ('start_ceiling_amps', 'shed_amps', 'shed_hold_seconds'),
            ),
        ],
        [(changes, NOW)],
    )


def test_load_settings_as_stored_write_and_journal_nothing(
    f_use_case: UpdateLoadSettingsUseCase,
    f_load_settings: LoadSettingsConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(EDIT)

    assert (output, f_load_settings.saved, f_recorder.records) == (UpdateLoadSettingsOutputDTO(changes=()), [], [])


def test_thresholds_out_of_order_are_refused(
    f_use_case: UpdateLoadSettingsUseCase,
    f_load_settings: LoadSettingsConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(replace(EDIT, restore_amps=Decimal('24.0'), start_ceiling_amps=Decimal('24.0')))

    assert (raised.value.problems, f_load_settings.saved, f_recorder.records) == (
        (SettingProblem(field='start_ceiling_amps', code='not_below_shed'),),
        [],
        [],
    )


def test_input_carries_no_load_management_gate() -> None:
    assert 'load_management_live' not in {field.name for field in fields(UpdateLoadSettingsInputDTO)}


def test_shed_threshold_saved_after_another_save_raised_the_start_ceiling_is_written_with_its_own_ceiling(
    f_use_case_after_the_start_ceiling_was_raised_meanwhile: UpdateLoadSettingsUseCase,
    f_load_settings: LoadSettingsConfigStoreStub,
) -> None:
    output = f_use_case_after_the_start_ceiling_was_raised_meanwhile.execute(replace(EDIT, shed_amps=Decimal('21.0')))

    lowered = replace(DEFAULTS, shed_amps=Decimal('21.0'))
    assert (output.changes, f_load_settings.saved[-1], f_load_settings.config_record_for_update().config) == (
        (_change('start_ceiling_amps', '22.0', '20.0'), _change('shed_amps', '24.0', '21.0')),
        (lowered, ('start_ceiling_amps', 'shed_amps')),
        lowered,
    )
