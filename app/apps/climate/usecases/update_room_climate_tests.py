from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext

from apps.climate.domain.room_climate import RoomClimateConfig, RoomClimateConfigRecord
from apps.climate.factories import RoomClimateFactory, ZoneFactory
from apps.climate.models import RoomClimate
from apps.climate.stubs import RoomClimateConfigStoreStub
from apps.climate.usecases.update_room_climate import (
    UpdateRoomClimateInputDTO,
    UpdateRoomClimateOutputDTO,
    UpdateRoomClimateUseCase,
)
from apps.core.domain.errors import SettingsRejectedError
from apps.core.domain.settings import SettingChange, SettingProblem
from apps.core.stubs import SettingsRecorderStub, TransactionStub
from apps.house.domain.errors import RoomNotFoundError
from apps.house.factories import RoomFactory
from apps.telemetry.models import JournalEntry

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
BEDROOM = RoomClimateConfig(
    comfort_target=Decimal('20.5'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    guest_room=False,
)
OPEN_SPACE = RoomClimateConfig(
    comfort_target=Decimal('22.0'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    guest_room=False,
)
BEDROOM_EDIT = UpdateRoomClimateInputDTO(
    room='bedroom',
    comfort_target=Decimal('20.5'),
    setback_target=Decimal('19.5'),
    backup_below=Decimal('19.0'),
    backup_hysteresis=Decimal('0.30'),
    guest_room=False,
    now=NOW,
)
OPEN_SPACE_EDIT = replace(
    BEDROOM_EDIT,
    room='open-space',
    comfort_target=Decimal('22.0'),
    backup_below=None,
    backup_hysteresis=None,
)


def _change(
    field: str,
    old: str | bool,
    new: str | bool,
    *,
    subject: str = 'bedroom',
    name: str = 'Спальня',
) -> SettingChange:
    return SettingChange(target='room', subject=subject, name=name, field=field, old=old, new=new)


@pytest.fixture
def f_rooms() -> RoomClimateConfigStoreStub:
    return RoomClimateConfigStoreStub(
        records=[
            RoomClimateConfigRecord(
                id=1,
                slug='open-space',
                name='Гостиная, кухня и прихожая',
                floor_name='1 этаж',
                config=OPEN_SPACE,
                air_sensor_slug=None,
                backup_zone_slug=None,
                backup_zone_name=None,
                backup_live=False,
            ),
            RoomClimateConfigRecord(
                id=2,
                slug='bedroom',
                name='Спальня',
                floor_name='2 этаж',
                config=BEDROOM,
                air_sensor_slug='air-second-floor',
                backup_zone_slug='bathroom-2f',
                backup_zone_name='Санузел 2 этаж',
                backup_live=True,
            ),
        ],
    )


@pytest.fixture
def f_rooms_with_a_guest_room(
    request: pytest.FixtureRequest,
    f_rooms: RoomClimateConfigStoreStub,
) -> RoomClimateConfigStoreStub:
    return RoomClimateConfigStoreStub(
        records=[
            replace(record, config=replace(record.config, guest_room=request.param))
            for record in f_rooms.config_records()
        ],
    )


@pytest.fixture
def f_recorder() -> SettingsRecorderStub:
    return SettingsRecorderStub(error=None)


@pytest.fixture
def f_transaction() -> TransactionStub:
    return TransactionStub(meanwhile=None)


@pytest.fixture
def f_use_case(
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    f_transaction: TransactionStub,
) -> UpdateRoomClimateUseCase:
    return UpdateRoomClimateUseCase(rooms=f_rooms, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_guest_room_use_case(
    f_rooms_with_a_guest_room: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    f_transaction: TransactionStub,
) -> UpdateRoomClimateUseCase:
    return UpdateRoomClimateUseCase(rooms=f_rooms_with_a_guest_room, recorder=f_recorder, transaction=f_transaction)


@pytest.fixture
def f_locked_use_case(f_rooms: RoomClimateConfigStoreStub) -> UpdateRoomClimateUseCase:
    return UpdateRoomClimateUseCase(
        rooms=f_rooms,
        recorder=SettingsRecorderStub(error=OperationalError('database is locked')),
        transaction=TransactionStub(meanwhile=None),
    )


@pytest.fixture
def f_use_case_after_the_hysteresis_was_widened_meanwhile(
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> UpdateRoomClimateUseCase:
    widened = replace(BEDROOM, backup_hysteresis=Decimal('0.50'))
    return UpdateRoomClimateUseCase(
        rooms=f_rooms,
        recorder=f_recorder,
        transaction=TransactionStub(meanwhile=partial(f_rooms.save_config, 'bedroom', widened, ('backup_hysteresis',))),
    )


@pytest.fixture
def f_stored_bedroom() -> RoomClimate:
    return RoomClimateFactory.create(
        room=RoomFactory.create(slug='bedroom', name='Спальня'),
        comfort_target=Decimal('20.5'),
        backup_zone=ZoneFactory.create(slug='bathroom-2f'),
    )


def test_changed_room_targets_and_backup_heat_are_saved_and_journaled_with_the_room_as_their_object(
    f_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    output = f_use_case.execute(
        replace(
            BEDROOM_EDIT,
            comfort_target=Decimal('21'),
            backup_below=Decimal('18.5'),
            backup_hysteresis=Decimal('0.5'),
        ),
    )

    changes = (
        _change('comfort_target', '20.5', '21.0'),
        _change('backup_below', '19.0', '18.5'),
        _change('backup_hysteresis', '0.30', '0.50'),
    )
    assert (output, f_rooms.saved, f_recorder.records) == (
        UpdateRoomClimateOutputDTO(changes=changes),
        [
            (
                'bedroom',
                replace(
                    BEDROOM,
                    comfort_target=Decimal('21.0'),
                    backup_below=Decimal('18.5'),
                    backup_hysteresis=Decimal('0.50'),
                ),
                ('comfort_target', 'backup_below', 'backup_hysteresis'),
            ),
        ],
        [(changes, NOW)],
    )


@pytest.mark.parametrize(
    ('f_rooms_with_a_guest_room', 'guest_room'),
    [(False, True), (True, False)],
    ids=['made_a_guest_room', 'no_longer_a_guest_room'],
    indirect=['f_rooms_with_a_guest_room'],
)
def test_guest_room_switch_is_saved_and_journaled_like_any_room_setting(
    f_guest_room_use_case: UpdateRoomClimateUseCase,
    f_rooms_with_a_guest_room: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    guest_room: bool,
) -> None:
    output = f_guest_room_use_case.execute(replace(OPEN_SPACE_EDIT, guest_room=guest_room))

    change = _change(
        'guest_room',
        not guest_room,
        guest_room,
        subject='open-space',
        name='Гостиная, кухня и прихожая',
    )
    assert (output.changes, f_rooms_with_a_guest_room.saved, f_recorder.records) == (
        (change,),
        [('open-space', replace(OPEN_SPACE, guest_room=guest_room), ('guest_room',))],
        [((change,), NOW)],
    )


def test_room_without_backup_heat_keeps_its_stored_backup_values(
    f_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
) -> None:
    f_use_case.execute(replace(OPEN_SPACE_EDIT, setback_target=Decimal('19.0')))

    changes = (_change('setback_target', '19.5', '19.0', subject='open-space', name='Гостиная, кухня и прихожая'),)
    assert (f_rooms.saved, f_recorder.records) == (
        [('open-space', replace(OPEN_SPACE, setback_target=Decimal('19.0')), ('setback_target',))],
        [(changes, NOW)],
    )


@pytest.mark.parametrize(
    'edit',
    [BEDROOM_EDIT, replace(BEDROOM_EDIT, comfort_target=Decimal('20.50'), backup_hysteresis=Decimal('0.3'))],
    ids=['as_stored', 'same_numbers_written_otherwise'],
)
def test_room_settings_as_stored_write_and_journal_nothing(
    f_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    edit: UpdateRoomClimateInputDTO,
) -> None:
    output = f_use_case.execute(edit)

    assert (output, f_rooms.saved, f_recorder.records) == (UpdateRoomClimateOutputDTO(changes=()), [], [])


@pytest.mark.parametrize(
    ('edit', 'problems'),
    [
        (
            replace(BEDROOM_EDIT, setback_target=Decimal('21.0')),
            (SettingProblem(field='setback_target', code='above_comfort'),),
        ),
        (
            replace(BEDROOM_EDIT, backup_below=Decimal('19.1')),
            (SettingProblem(field='backup_below', code='not_below_setback'),),
        ),
        (
            replace(OPEN_SPACE_EDIT, comfort_target=Decimal('41.0')),
            (SettingProblem(field='comfort_target', code='out_of_range'),),
        ),
    ],
    ids=['night_above_comfort', 'backup_heat_too_close_to_the_night_target', 'out_of_range'],
)
def test_room_settings_out_of_order_are_refused_and_nothing_is_written(
    f_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
    f_recorder: SettingsRecorderStub,
    edit: UpdateRoomClimateInputDTO,
    problems: tuple[SettingProblem, ...],
) -> None:
    with pytest.raises(SettingsRejectedError) as raised:
        f_use_case.execute(edit)

    assert (raised.value.problems, f_rooms.saved, f_recorder.records) == (problems, [], [])


def test_backup_heat_of_a_room_without_a_backup_zone_is_not_checked_against_its_night_target(
    f_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
) -> None:
    f_use_case.execute(replace(OPEN_SPACE_EDIT, setback_target=Decimal('18.0')))

    assert f_rooms.saved == [('open-space', replace(OPEN_SPACE, setback_target=Decimal('18.0')), ('setback_target',))]


def test_settings_of_an_unknown_room_raise(f_use_case: UpdateRoomClimateUseCase) -> None:
    with pytest.raises(RoomNotFoundError):
        f_use_case.execute(replace(BEDROOM_EDIT, room='attic'))


def test_room_change_the_journal_refuses_is_not_written(
    f_locked_use_case: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
) -> None:
    with pytest.raises(OperationalError):
        f_locked_use_case.execute(replace(BEDROOM_EDIT, comfort_target=Decimal('21.0')))

    assert f_rooms.saved == []


@pytest.mark.parametrize(
    ('edit', 'outcomes'),
    [
        (replace(BEDROOM_EDIT, comfort_target=Decimal('21.0')), ['committed']),
        (replace(BEDROOM_EDIT, backup_below=Decimal('19.5')), ['rolled_back']),
    ],
    ids=['saved', 'refused'],
)
def test_room_settings_are_read_checked_written_and_journaled_in_one_transaction(
    f_use_case: UpdateRoomClimateUseCase,
    f_transaction: TransactionStub,
    edit: UpdateRoomClimateInputDTO,
    outcomes: list[str],
) -> None:
    with suppress(SettingsRejectedError):
        f_use_case.execute(edit)

    assert f_transaction.outcomes == outcomes


def test_room_edit_without_backup_values_keeps_those_another_save_stored_meanwhile(
    f_use_case_after_the_hysteresis_was_widened_meanwhile: UpdateRoomClimateUseCase,
    f_rooms: RoomClimateConfigStoreStub,
) -> None:
    output = f_use_case_after_the_hysteresis_was_widened_meanwhile.execute(
        replace(BEDROOM_EDIT, comfort_target=Decimal('21.0'), backup_below=None, backup_hysteresis=None),
    )

    assert (output.changes, f_rooms.saved[-1]) == (
        (_change('comfort_target', '20.5', '21.0'),),
        (
            'bedroom',
            replace(BEDROOM, comfort_target=Decimal('21.0'), backup_hysteresis=Decimal('0.50')),
            ('comfort_target',),
        ),
    )


@pytest.mark.django_db
def test_stored_room_climate_is_locked_before_it_is_read_and_its_change_is_journaled_in_the_same_transaction(
    f_stored_bedroom: RoomClimate,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        UpdateRoomClimateUseCase.init().execute(replace(BEDROOM_EDIT, comfort_target=Decimal('21.0')))

    statements = [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]
    assert (
        statements[:3],
        statements[-1],
        RoomClimate.objects.get(pk=f_stored_bedroom.pk).comfort_target,
        list(JournalEntry.objects.values_list('kind', 'subject')),
    ) == (['SAVEPOINT', 'UPDATE', 'SELECT'], 'RELEASE', Decimal('21.0'), [('settings_changed', 'bedroom')])
