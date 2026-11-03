from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

LOADS = (
    ('water-heater', 'plug', 'plug-water-heater', '6.0'),
    ('bathroom', 'zone', 'bathroom', None),
    ('living-room', 'zone', 'living-room', None),
)
START_CEILING_AMPS = '20.0'
SHED_AMPS = '24.0'
SHED_HOLD_SECONDS = 3
RESTORE_AMPS = '18.0'
RESTORE_HOLD_SECONDS = 120
SHED_INTERVAL_SECONDS = 1
RESTORE_INTERVAL_SECONDS = 60
START_INTERVAL_SECONDS = 30
METER_LOST_AFTER_SECONDS = 60
DEFAULT_LOAD_AMPS = '6.0'


def create_loads(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    device_model = apps.get_model('devices', 'Device')
    load_model = apps.get_model('power', 'Load')
    settings_model = apps.get_model('power', 'LoadSettings')
    if not settings_model.objects.exists():
        settings_model.objects.create(
            start_ceiling_amps=Decimal(START_CEILING_AMPS),
            shed_amps=Decimal(SHED_AMPS),
            shed_hold_seconds=SHED_HOLD_SECONDS,
            restore_amps=Decimal(RESTORE_AMPS),
            restore_hold_seconds=RESTORE_HOLD_SECONDS,
            shed_interval_seconds=SHED_INTERVAL_SECONDS,
            restore_interval_seconds=RESTORE_INTERVAL_SECONDS,
            start_interval_seconds=START_INTERVAL_SECONDS,
            meter_lost_after_seconds=METER_LOST_AFTER_SECONDS,
            default_load_amps=Decimal(DEFAULT_LOAD_AMPS),
        )
    for priority, (slug, kind, target, nominal_amps) in enumerate(LOADS, start=1):
        zone = zone_model.objects.filter(slug=target).first() if kind == 'zone' else None
        device = device_model.objects.filter(slug=target).first() if kind == 'plug' else None
        controlled = zone or device
        if controlled is None:
            continue
        load_model.objects.create(
            slug=slug,
            name=controlled.name,
            kind=kind,
            zone=zone,
            device=device,
            priority=priority,
            nominal_amps=None if nominal_amps is None else Decimal(nominal_amps),
        )


def delete_loads(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('power', 'Load').objects.filter(slug__in=[load[0] for load in LOADS]).delete()
    apps.get_model('power', 'LoadSettings').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_example', '0004_air_control'),
        ('power', '0003_load_management'),
    ]

    operations = [migrations.RunPython(create_loads, delete_loads)]
