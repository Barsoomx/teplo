from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

AIR_ZONES = (('living-room', '22.0', '19.5', '18.0', '30.0'),)
AIR_START_OFFSET = '0.3'
INITIAL_EARLY_STOP_OFFSET = '0.3'
PREHEAT_LEAD_CAP_MINUTES = 240
LEARNED_MORNINGS_NEEDED = 3
SECOND_FLOOR_GUARD_ZONE = 'bathroom'
SECOND_FLOOR_GUARD_THRESHOLD = '19.0'
SECOND_FLOOR_GUARD_HYSTERESIS = '0.3'


def seed_air_control(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    settings_model = apps.get_model('climate', 'ClimateSettings')
    for slug, comfort, night, idle, limit in AIR_ZONES:
        zone_model.objects.filter(slug=slug).update(
            comfort_air_target=Decimal(comfort),
            night_air_target=Decimal(night),
            idle_floor_setpoint=Decimal(idle),
            floor_limit_setpoint=Decimal(limit),
        )
    settings_model.objects.update(
        air_start_offset=Decimal(AIR_START_OFFSET),
        initial_early_stop_offset=Decimal(INITIAL_EARLY_STOP_OFFSET),
        preheat_lead_cap_minutes=PREHEAT_LEAD_CAP_MINUTES,
        learned_mornings_needed=LEARNED_MORNINGS_NEEDED,
        second_floor_guard_zone=zone_model.objects.filter(slug=SECOND_FLOOR_GUARD_ZONE).first(),
        second_floor_guard_threshold=Decimal(SECOND_FLOOR_GUARD_THRESHOLD),
        second_floor_guard_hysteresis=Decimal(SECOND_FLOOR_GUARD_HYSTERESIS),
    )


def clear_guard_zone(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'ClimateSettings').objects.update(second_floor_guard_zone=None)


class Migration(migrations.Migration):
    dependencies = [
        ('site_example', '0003_sensors'),
        ('climate', '0005_climate_control'),
    ]

    operations = [migrations.RunPython(seed_air_control, clear_guard_zone)]
