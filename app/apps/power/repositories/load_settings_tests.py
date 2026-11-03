from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.factories import LoadSettingsFactory
from apps.power.models import LoadSettings
from apps.power.repositories.load_settings import LoadSettingsRepository

pytestmark = pytest.mark.django_db


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
