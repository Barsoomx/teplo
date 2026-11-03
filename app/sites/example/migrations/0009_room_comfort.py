from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

ROOM_ZONES = (
    ('living-room', 'living-room'),
    ('bathroom', 'bathroom'),
)
BEDROOM = 'bedroom'
BEDROOM_COMFORT_AT_LEAST = Decimal('20.5')
BEDROOM_SETBACK_AT_LEAST = Decimal('19.5')
BACKUP_MARGIN = Decimal('0.5')
GUARD_THRESHOLD = Decimal('19.0')


def move_comfort(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    climate_model = apps.get_model('climate', 'RoomClimate')
    rooms = {room.slug: room for room in apps.get_model('house', 'Room').objects.all()}
    zones = {zone.slug: zone for zone in zone_model.objects.all()}
    for room_slug, zone_slug in ROOM_ZONES:
        if room_slug not in rooms:
            continue
        zone = zones.get(zone_slug)
        if zone is None:
            climate_model.objects.create(room=rooms[room_slug])
            continue
        climate = climate_model.objects.create(
            room=rooms[room_slug],
            comfort_target=zone.comfort_air_target,
            setback_target=min(zone.night_air_target, zone.comfort_air_target),
        )
        period_model.objects.filter(zone=zone).update(room_climate=climate)
    if BEDROOM in rooms:
        climate_model.objects.create(room=rooms[BEDROOM], **_bedroom(apps))


def restore_comfort(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    climate_model = apps.get_model('climate', 'RoomClimate')
    bedroom = climate_model.objects.filter(room__slug=BEDROOM).first()
    if bedroom is not None:
        apps.get_model('climate', 'ClimateSettings').objects.update(
            second_floor_sensor_id=bedroom.air_sensor_id,
            second_floor_guard_zone_id=bedroom.backup_zone_id,
            second_floor_guard_threshold=bedroom.backup_below,
            second_floor_guard_hysteresis=bedroom.backup_hysteresis,
            second_floor_guard_live=bedroom.backup_live,
        )
    for climate in climate_model.objects.all():
        zones = list(zone_model.objects.filter(room_id=climate.room_id))
        windows = list(period_model.objects.filter(room_climate=climate).values_list('weekday', 'start', 'end'))
        for zone in zones:
            period_model.objects.filter(zone=zone).delete()
            period_model.objects.bulk_create(
                [period_model(zone=zone, weekday=weekday, start=start, end=end) for weekday, start, end in windows],
            )
            zone_model.objects.filter(pk=zone.pk).update(
                comfort_air_target=climate.comfort_target,
                night_air_target=climate.setback_target,
            )
    period_model.objects.filter(zone__isnull=True).delete()
    period_model.objects.update(room_climate=None)
    climate_model.objects.all().delete()


def _bedroom(apps: StateApps) -> dict[str, object]:
    settings = apps.get_model('climate', 'ClimateSettings').objects.order_by('pk').first()
    threshold = GUARD_THRESHOLD if settings is None else settings.second_floor_guard_threshold
    setback = max(BEDROOM_SETBACK_AT_LEAST, threshold + BACKUP_MARGIN)
    targets = {'comfort_target': max(BEDROOM_COMFORT_AT_LEAST, setback), 'setback_target': setback}
    if settings is None:
        return targets

    return {
        **targets,
        'air_sensor_id': settings.second_floor_sensor_id,
        'backup_zone_id': settings.second_floor_guard_zone_id,
        'backup_below': threshold,
        'backup_hysteresis': settings.second_floor_guard_hysteresis,
        'backup_live': settings.second_floor_guard_live,
    }


class Migration(migrations.Migration):
    dependencies = [
        ('site_example', '0008_house'),
        ('climate', '0010_room_climate'),
    ]

    operations = [migrations.RunPython(move_comfort, restore_comfort)]
