from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest

from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub
from apps.power.domain.errors import LoadNotFoundError
from apps.power.domain.load_config import LoadConfig, LoadConfigRecord
from apps.power.domain.loads import LoadKind, LoadPhase
from apps.power.stubs import LoadConfigStoreStub
from apps.power.usecases.update_loads import LoadEdit, UpdateLoadsInputDTO, UpdateLoadsOutputDTO, UpdateLoadsUseCase

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
BOILER = LoadConfig(priority=1, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0'))
KITCHEN = LoadConfig(priority=2, phase=LoadPhase.L2, nominal_amps=None)
HALLWAY = LoadConfig(priority=3, phase=LoadPhase.UNKNOWN, nominal_amps=None)


@pytest.fixture
def f_loads() -> LoadConfigStoreStub:
    return LoadConfigStoreStub(
        records=[
            LoadConfigRecord(
                id=1,
                slug='boiler-1f',
                name='Бойлер 1 этаж',
                kind=LoadKind.PLUG,
                config=BOILER,
                live=True,
            ),
            LoadConfigRecord(id=2, slug='kitchen', name='Кухня', kind=LoadKind.ZONE, config=KITCHEN, live=False),
            LoadConfigRecord(id=3, slug='hallway', name='Прихожая', kind=LoadKind.ZONE, config=HALLWAY, live=False),
        ],
    )


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_use_case(f_loads: LoadConfigStoreStub, f_recorder: SettingsRecorderStub) -> UpdateLoadsUseCase:
    return UpdateLoadsUseCase(loads=f_loads, recorder=f_recorder, transaction=TransactionStub(meanwhile=None))


@pytest.fixture
def f_use_case_after_the_hallway_moved_meanwhile(
    f_loads: LoadConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateLoadsUseCase:
    moved = {'hallway': replace(HALLWAY, priority=4)}
    return UpdateLoadsUseCase(
        loads=f_loads,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_loads.save_configs, moved)),
    )


def test_swapped_priorities_are_saved_together_and_journaled_per_load(
    f_use_case: UpdateLoadsUseCase,
    f_loads: LoadConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        UpdateLoadsInputDTO(
            loads=(
                LoadEdit(
                    slug='boiler-1f',
                    config=LoadConfig(priority=2, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                ),
                LoadEdit(slug='kitchen', config=LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None)),
                LoadEdit(slug='hallway', config=HALLWAY),
            ),
            now=NOW,
        ),
    )

    changes = (
        SettingChange(target='load', subject='boiler-1f', name='Бойлер 1 этаж', field='priority', old=1, new=2),
        SettingChange(target='load', subject='kitchen', name='Кухня', field='priority', old=2, new=1),
    )
    assert (output, f_loads.saved, f_recorder.records) == (
        UpdateLoadsOutputDTO(changes=changes),
        [
            {
                'boiler-1f': LoadConfig(priority=2, phase=LoadPhase.UNKNOWN, nominal_amps=Decimal('6.0')),
                'kitchen': LoadConfig(priority=1, phase=LoadPhase.L2, nominal_amps=None),
            },
        ],
        [(changes, NOW)],
    )


def test_phase_and_nominal_current_of_a_load_are_journaled(
    f_use_case: UpdateLoadsUseCase,
    f_recorder: SettingsRecorderStub,
) -> None:
    f_use_case.execute(
        UpdateLoadsInputDTO(
            loads=(LoadEdit(slug='boiler-1f', config=LoadConfig(priority=1, phase=LoadPhase.L3, nominal_amps=None)),),
            now=NOW,
        ),
    )

    assert f_recorder.records == [
        (
            (
                SettingChange(
                    target='load',
                    subject='boiler-1f',
                    name='Бойлер 1 этаж',
                    field='phase',
                    old='unknown',
                    new='l3',
                ),
                SettingChange(
                    target='load',
                    subject='boiler-1f',
                    name='Бойлер 1 этаж',
                    field='nominal_amps',
                    old='6.0',
                    new=None,
                ),
            ),
            NOW,
        ),
    ]


def test_priority_another_load_keeps_is_refused_for_both(
    f_use_case: UpdateLoadsUseCase,
    f_loads: LoadConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(
            UpdateLoadsInputDTO(
                loads=(LoadEdit(slug='kitchen', config=LoadConfig(priority=3, phase=LoadPhase.L2, nominal_amps=None)),),
                now=NOW,
            ),
        )

    assert (raised.value.problems, f_loads.saved, f_recorder.records) == (
        (
            SettingProblem(field='priority', code='priority_taken', subject='kitchen'),
            SettingProblem(field='priority', code='priority_taken', subject='hallway'),
        ),
        [],
        [],
    )


def test_loads_as_stored_write_and_journal_nothing(
    f_use_case: UpdateLoadsUseCase,
    f_loads: LoadConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        UpdateLoadsInputDTO(
            loads=(LoadEdit(slug='boiler-1f', config=BOILER), LoadEdit(slug='kitchen', config=KITCHEN)),
            now=NOW,
        ),
    )

    assert (output, f_loads.saved, f_recorder.records) == (UpdateLoadsOutputDTO(changes=()), [], [])


def test_unknown_load_raises(f_use_case: UpdateLoadsUseCase, f_loads: LoadConfigStoreStub) -> None:
    with pytest.raises(LoadNotFoundError):
        f_use_case.execute(UpdateLoadsInputDTO(loads=(LoadEdit(slug='attic', config=BOILER),), now=NOW))

    assert f_loads.saved == []


def test_load_edit_carries_no_live_gate() -> None:
    assert 'live' not in {field.name for field in (*fields(LoadEdit), *fields(LoadConfig))}


def test_priority_another_save_took_meanwhile_is_refused_and_nothing_more_is_written(
    f_use_case_after_the_hallway_moved_meanwhile: UpdateLoadsUseCase,
    f_loads: LoadConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case_after_the_hallway_moved_meanwhile.execute(
            UpdateLoadsInputDTO(loads=(LoadEdit(slug='boiler-1f', config=replace(BOILER, priority=4)),), now=NOW),
        )

    assert (raised.value.problems, f_loads.saved, f_recorder.records) == (
        (
            SettingProblem(field='priority', code='priority_taken', subject='boiler-1f'),
            SettingProblem(field='priority', code='priority_taken', subject='hallway'),
        ),
        [{'hallway': replace(HALLWAY, priority=4)}],
        [],
    )
