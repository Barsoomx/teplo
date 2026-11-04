from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

FLOORS = (
    ('floor-1', '1st floor', 1),
    ('floor-2', '2nd floor', 2),
)
ROOMS = (
    ('living-room', 'Living room', 'floor-1', 1),
    ('bathroom', 'Bathroom', 'floor-1', 2),
    ('bedroom', 'Bedroom', 'floor-2', 1),
)
ZONE_ROOMS = (
    ('living-room', 'living-room'),
    ('bathroom', 'bathroom'),
)
DEVICE_ROOMS = (
    ('thermostat-living-room', 'living-room'),
    ('air-living-room', 'living-room'),
    ('thermostat-bathroom', 'bathroom'),
    ('plug-water-heater', 'bathroom'),
    ('air-second-floor', 'bedroom'),
)
BEDROOM_AIR = 'air-second-floor'
BEDROOM_AIR_OLD_NAME = 'Second floor air sensor'
BEDROOM_AIR_NAME = 'Bedroom air sensor'


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
    floors = [floor[0] for floor in FLOORS]
    room_model = apps.get_model('house', 'Room')
    room_model.objects.filter(floor__slug__in=floors).delete()
    room_model.objects.filter(slug__in=[room[0] for room in ROOMS]).delete()
    apps.get_model('house', 'Floor').objects.filter(slug__in=floors).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_example', '0007_air_min_band'),
        ('house', '0001_initial'),
        ('devices', '0003_device_room'),
        ('climate', '0009_zone_room'),
    ]

    operations = [migrations.RunPython(place_house, unplace_house)]
