from datetime import time
from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

THERMOSTATS = (
    ('thermostat-living-room', 'Living room thermostat'),
    ('thermostat-bathroom', 'Bathroom thermostat'),
)
ZONES = (
    ('living-room', 'Living room', 'air', 'thermostat-living-room', '28.0', '22.0', '30.0'),
    ('bathroom', 'Bathroom', 'floor', 'thermostat-bathroom', '30.0', '22.0', '30.0'),
)
WEEKDAY_COMFORT = (time(6, 30), time(23, 0))
WEEKEND_COMFORT = (time(8, 0), time(23, 30))


def create_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    devices = {
        slug: device_model.objects.create(slug=slug, name=name, kind='thermostat', driver='tuya_thermostat')
        for slug, name in THERMOSTATS
    }
    for slug, name, kind, thermostat, comfort, night, boost in ZONES:
        zone = zone_model.objects.create(
            slug=slug,
            name=name,
            kind=kind,
            thermostat=devices[thermostat],
            comfort_floor_setpoint=Decimal(comfort),
            night_floor_setpoint=Decimal(night),
            boost_floor_setpoint=Decimal(boost),
        )
        period_model.objects.bulk_create(
            [
                period_model(
                    zone=zone,
                    weekday=weekday,
                    start=(WEEKEND_COMFORT if weekday >= 5 else WEEKDAY_COMFORT)[0],
                    end=(WEEKEND_COMFORT if weekday >= 5 else WEEKDAY_COMFORT)[1],
                )
                for weekday in range(7)
            ],
        )


def delete_site(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'Zone').objects.filter(slug__in=[zone[0] for zone in ZONES]).delete()
    apps.get_model('devices', 'Device').objects.filter(slug__in=[device[0] for device in THERMOSTATS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('devices', '0001_initial'),
        ('climate', '0001_initial'),
    ]

    operations = [migrations.RunPython(create_site, delete_site)]
