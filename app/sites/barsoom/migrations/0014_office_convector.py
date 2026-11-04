from datetime import time
from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Max

OFFICE = 'office'
CONVECTOR_NAME = 'Конвектор: кабинет'
HEATER_DEVICE = 'heater-office'
CONVECTOR = 'convector-office'
CONVECTOR_PHASE = 'l2'
SETPOINT_BIAS = Decimal('0.0')
MAX_SETPOINT = Decimal('24.0')
MIN_WRITE_MINUTES = 5
RATED_WATTS = 2000
COMFORT_START = time(13, 0)
COMFORT_END = time(23, 45)
WEEKDAYS = range(7)


def add_convector(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    office = apps.get_model('house', 'Room').objects.filter(slug=OFFICE).first()
    if office is None:
        return

    device, _ = apps.get_model('devices', 'Device').objects.get_or_create(
        slug=HEATER_DEVICE,
        defaults={'name': CONVECTOR_NAME, 'kind': 'climate_unit', 'driver': 'tuya_heater', 'room': office},
    )
    source, _ = apps.get_model('climate', 'HeatSource').objects.get_or_create(
        slug=CONVECTOR,
        defaults={
            'name': CONVECTOR_NAME,
            'room': office,
            'kind': 'heater',
            'device': device,
            'priority': 1,
            'live': False,
            'setpoint_bias': SETPOINT_BIAS,
            'max_setpoint': MAX_SETPOINT,
            'min_write_minutes': MIN_WRITE_MINUTES,
            'rated_watts': RATED_WATTS,
        },
    )
    load_model = apps.get_model('power', 'Load')
    if not load_model.objects.filter(slug=CONVECTOR).exists():
        top = load_model.objects.aggregate(top=Max('priority'))['top'] or 0
        load_model.objects.create(
            slug=CONVECTOR,
            name=CONVECTOR_NAME,
            kind='source',
            heat_source=source,
            priority=top + 1,
            phase=CONVECTOR_PHASE,
            live=False,
        )
    _add_office_windows(apps, office)


def _add_office_windows(apps: StateApps, office: object) -> None:
    climate = apps.get_model('climate', 'RoomClimate').objects.filter(room=office).first()
    period_model = apps.get_model('climate', 'ComfortPeriod')
    if climate is None or period_model.objects.filter(room_climate=climate).exists():
        return

    period_model.objects.bulk_create(
        period_model(room_climate=climate, weekday=weekday, start=COMFORT_START, end=COMFORT_END)
        for weekday in WEEKDAYS
    )


def remove_convector(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    _refuse_other_heaters(apps)
    apps.get_model('power', 'Load').objects.filter(slug=CONVECTOR).delete()
    apps.get_model('climate', 'HeatSource').objects.filter(slug=CONVECTOR).delete()
    apps.get_model('devices', 'Device').objects.filter(slug=HEATER_DEVICE).delete()


def _refuse_other_heaters(apps: StateApps) -> None:
    sources = apps.get_model('climate', 'HeatSource').objects.exclude(slug=CONVECTOR).exclude(kind='fireplace')
    loads = apps.get_model('power', 'Load').objects.filter(kind='source').exclude(slug=CONVECTOR)
    devices = apps.get_model('devices', 'Device').objects.filter(driver='tuya_heater').exclude(slug=HEATER_DEVICE)
    others = sorted(
        {
            *sources.values_list('slug', flat=True),
            *loads.values_list('slug', flat=True),
            *devices.values_list('slug', flat=True),
        },
    )
    if others:
        raise RuntimeError(
            'an older release knows no heater but the office convector; delete in the admin the heat sources, '
            f'loads and devices added for another heater before this rollback: {", ".join(others)}',
        )


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0013_fireplace'),
        ('climate', '0014_heat_source_commands'),
        ('power', '0008_source_loads'),
        ('devices', '0004_tuya_heater'),
    ]

    operations = [migrations.RunPython(add_convector, remove_convector)]
