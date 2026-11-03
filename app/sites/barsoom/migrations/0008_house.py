from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

FLOORS = (
    ('floor-1', '1 этаж', 1),
    ('floor-2', '2 этаж', 2),
)
ROOMS = (
    ('open-space', 'Гостиная, кухня и прихожая', 'floor-1', 1),
    ('bathroom-1f', 'Санузел 1 этаж', 'floor-1', 2),
    ('bedroom', 'Спальня', 'floor-2', 1),
    ('office', 'Кабинет', 'floor-2', 2),
    ('bathroom-2f', 'Санузел 2 этаж', 'floor-2', 3),
)
ZONE_ROOMS = (
    ('living-room', 'open-space'),
    ('kitchen', 'open-space'),
    ('hallway', 'open-space'),
    ('bathroom-1f', 'bathroom-1f'),
    ('bathroom-2f', 'bathroom-2f'),
)
MONITOR_ONLY_PLUGS = (
    ('plug-oven', 'Духовка'),
    ('plug-dishwasher', 'Посудомоечная машина'),
    ('plug-washing-machine', 'Стиральная машина'),
)
DEVICE_ROOMS = (
    ('thermostat-living-room', 'open-space'),
    ('thermostat-kitchen', 'open-space'),
    ('thermostat-hallway', 'open-space'),
    ('air-living-room', 'open-space'),
    ('air-kitchen', 'open-space'),
    ('air-hallway', 'open-space'),
    ('plug-oven', 'open-space'),
    ('plug-dishwasher', 'open-space'),
    ('thermostat-bathroom-1f', 'bathroom-1f'),
    ('plug-boiler-1f', 'bathroom-1f'),
    ('plug-washing-machine', 'bathroom-1f'),
    ('air-second-floor', 'bedroom'),
    ('thermostat-bathroom-2f', 'bathroom-2f'),
    ('plug-boiler-2f', 'bathroom-2f'),
)
BEDROOM_AIR = 'air-second-floor'
BEDROOM_AIR_OLD_NAME = 'Воздух: 2 этаж'
BEDROOM_AIR_NAME = 'Воздух: спальня'


def place_house(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    floor_model = apps.get_model('house', 'Floor')
    room_model = apps.get_model('house', 'Room')
    zone_model = apps.get_model('climate', 'Zone')
    device_model = apps.get_model('devices', 'Device')
    floors = {
        slug: floor_model.objects.update_or_create(slug=slug, defaults={'name': name, 'level': level})[0]
        for slug, name, level in FLOORS
    }
    rooms = {
        slug: room_model.objects.update_or_create(
            slug=slug,
            defaults={'name': name, 'floor': floors[floor], 'position': position},
        )[0]
        for slug, name, floor, position in ROOMS
    }
    for slug, name in MONITOR_ONLY_PLUGS:
        device_model.objects.get_or_create(
            slug=slug,
            defaults={'name': name, 'kind': 'plug', 'driver': 'tuya_plug', 'monitor_only': True},
        )
    for zone, room in ZONE_ROOMS:
        zone_model.objects.filter(slug=zone).update(room=rooms[room])
    for device, room in DEVICE_ROOMS:
        device_model.objects.filter(slug=device).update(room=rooms[room])
    device_model.objects.filter(slug=BEDROOM_AIR, name=BEDROOM_AIR_OLD_NAME).update(name=BEDROOM_AIR_NAME)


def unplace_house(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    device_model.objects.filter(slug=BEDROOM_AIR, name=BEDROOM_AIR_NAME).update(name=BEDROOM_AIR_OLD_NAME)
    apps.get_model('climate', 'Zone').objects.update(room=None)
    device_model.objects.update(room=None)
    apps.get_model('house', 'Room').objects.filter(slug__in=[room[0] for room in ROOMS]).delete()
    apps.get_model('house', 'Floor').objects.filter(slug__in=[floor[0] for floor in FLOORS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0007_air_min_band'),
        ('house', '0001_initial'),
        ('devices', '0003_device_room'),
        ('climate', '0009_zone_room'),
    ]

    operations = [migrations.RunPython(place_house, unplace_house)]
