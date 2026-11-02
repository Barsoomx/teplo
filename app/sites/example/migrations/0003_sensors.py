from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

SENSORS = (
    ('air-living-room', 'Living room air sensor', 'temperature_sensor', 'esphome_temperature'),
    ('air-second-floor', 'Second floor air sensor', 'temperature_sensor', 'esphome_temperature'),
    ('outdoor', 'Outdoor sensor', 'temperature_sensor', 'esphome_temperature'),
    ('phase-meter', 'Phase meter', 'phase_meter', 'esphome_phase_meter'),
)
AIR_SENSORS = (('living-room', 'air-living-room'),)
OUTDOOR_SENSOR = 'outdoor'
SECOND_FLOOR_SENSOR = 'air-second-floor'


def create_sensors(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    zone_model = apps.get_model('climate', 'Zone')
    settings_model = apps.get_model('climate', 'ClimateSettings')
    devices = {
        slug: device_model.objects.create(slug=slug, name=name, kind=kind, driver=driver)
        for slug, name, kind, driver in SENSORS
    }
    for zone, sensor in AIR_SENSORS:
        zone_model.objects.filter(slug=zone).update(air_sensor=devices[sensor])
    settings_model.objects.create(
        outdoor_sensor=devices[OUTDOOR_SENSOR],
        second_floor_sensor=devices[SECOND_FLOOR_SENSOR],
    )


def delete_sensors(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'ClimateSettings').objects.all().delete()
    apps.get_model('devices', 'Device').objects.filter(slug__in=[sensor[0] for sensor in SENSORS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_example', '0002_water_heater_plug'),
        ('climate', '0003_air_sensors'),
    ]

    operations = [migrations.RunPython(create_sensors, delete_sensors)]
