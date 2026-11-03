from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext

from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.load_config import LoadSettingsConfig, LoadSettingsRecord
from apps.power.factories import LoadSettingsFactory
from apps.power.models import LoadSettings
from apps.power.repositories.load_settings import LoadSettingsRepository

pytestmark = pytest.mark.django_db

DEFAULT_CONFIG = LoadSettingsConfig(
    start_ceiling_amps=Decimal('20.0'),
    shed_amps=Decimal('24.0'),
    shed_hold_seconds=3,
    restore_amps=Decimal('18.0'),
    restore_hold_seconds=120,
    start_interval_seconds=30,
    meter_lost_after_seconds=60,
    default_load_amps=Decimal('6.0'),
)


@pytest.fixture
def f_live_settings() -> LoadSettings:
    return LoadSettingsFactory.create(load_management_live=True, shed_interval_seconds=2, restore_interval_seconds=90)


@pytest.fixture
def tuned_settings() -> LoadSettings:
    return LoadSettingsFactory.create(
        load_management_live=True,
        start_ceiling_amps=Decimal('19.0'),
        shed_amps=Decimal('23.5'),
        shed_hold_seconds=5,
        restore_amps=Decimal('17.0'),
        restore_hold_seconds=180,
        shed_interval_seconds=2,
        restore_interval_seconds=90,
        start_interval_seconds=45,
        meter_lost_after_seconds=90,
        default_load_amps=Decimal('7.5'),
    )


def test_control_without_load_settings_takes_the_defaults_of_section_seven() -> None:
    assert LoadSettingsRepository.control() == LoadControl(
        live=False,
        thresholds=PhaseThresholds(
            start_ceiling=Decimal('20.0'),
            shed=Decimal('24.0'),
            shed_hold=timedelta(seconds=3),
            restore=Decimal('18.0'),
            restore_hold=timedelta(minutes=2),
            shed_interval=timedelta(seconds=1),
            restore_interval=timedelta(minutes=1),
            start_interval=timedelta(seconds=30),
            meter_lost_after=timedelta(minutes=1),
        ),
        default_amps=Decimal('6.0'),
    )


@pytest.mark.usefixtures('tuned_settings')
def test_control_reads_the_stored_load_settings() -> None:
    assert LoadSettingsRepository.control() == LoadControl(
        live=True,
        thresholds=PhaseThresholds(
            start_ceiling=Decimal('19.0'),
            shed=Decimal('23.5'),
            shed_hold=timedelta(seconds=5),
            restore=Decimal('17.0'),
            restore_hold=timedelta(minutes=3),
            shed_interval=timedelta(seconds=2),
            restore_interval=timedelta(seconds=90),
            start_interval=timedelta(seconds=45),
            meter_lost_after=timedelta(seconds=90),
        ),
        default_amps=Decimal('7.5'),
    )


@pytest.mark.parametrize(
    'overrides',
    [{'restore_amps': Decimal('20.5')}, {'start_ceiling_amps': Decimal('24.0')}],
    ids=['restore_above_the_start_ceiling', 'start_ceiling_at_the_shed_threshold'],
)
def test_database_refuses_thresholds_out_of_order(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(IntegrityError):
        LoadSettingsFactory.create(**overrides)


def test_restore_threshold_may_reach_the_start_ceiling() -> None:
    LoadSettingsFactory.create(restore_amps=Decimal('20.0'))

    assert LoadSettingsRepository.control().thresholds.restore == Decimal('20.0')


def test_config_record_without_load_settings_takes_the_defaults() -> None:
    assert LoadSettingsRepository.config_record() == LoadSettingsRecord(
        id=None,
        config=DEFAULT_CONFIG,
        load_management_live=False,
    )


def test_config_record_reads_the_stored_settings_and_the_gate(f_live_settings: LoadSettings) -> None:
    assert LoadSettingsRepository.config_record() == LoadSettingsRecord(
        id=f_live_settings.pk,
        config=DEFAULT_CONFIG,
        load_management_live=True,
    )


def test_saved_config_writes_the_named_fields_and_keeps_the_gate(f_live_settings: LoadSettings) -> None:
    config = replace(DEFAULT_CONFIG, start_ceiling_amps=Decimal('25.0'), shed_amps=Decimal('26.0'), shed_hold_seconds=5)

    LoadSettingsRepository.save_config(config, ('start_ceiling_amps', 'shed_amps'))

    stored = LoadSettings.objects.get(pk=f_live_settings.pk)
    assert (
        stored.start_ceiling_amps,
        stored.shed_amps,
        stored.shed_hold_seconds,
        stored.load_management_live,
        stored.shed_interval_seconds,
        stored.restore_interval_seconds,
    ) == (Decimal('25.0'), Decimal('26.0'), 3, True, 2, 90)


def test_saved_config_without_stored_settings_creates_them_with_load_management_off() -> None:
    LoadSettingsRepository.save_config(
        replace(DEFAULT_CONFIG, default_load_amps=Decimal('7.5')),
        ('default_load_amps',),
    )

    stored = LoadSettings.objects.get()
    assert (stored.default_load_amps, stored.shed_amps, stored.load_management_live) == (
        Decimal('7.5'),
        Decimal('24.0'),
        False,
    )


def test_saved_config_refuses_to_write_the_load_management_gate() -> None:
    with pytest.raises(KeyError):
        LoadSettingsRepository.save_config(DEFAULT_CONFIG, ('load_management_live',))


def test_config_record_for_update_takes_the_write_lock_before_it_reads_the_settings(
    f_live_settings: LoadSettings,
) -> None:
    with CaptureQueriesContext(connection) as queries:
        record = LoadSettingsRepository.config_record_for_update()

    assert (record.id, [query['sql'].split(' ', 1)[0] for query in queries.captured_queries]) == (
        f_live_settings.pk,
        ['UPDATE', 'SELECT'],
    )
