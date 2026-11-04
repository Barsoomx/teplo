from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Model

OPEN_SPACE_ZONES = ('living-room', 'kitchen', 'hallway')
ROOM_ZONES = (
    ('open-space', 'living-room'),
    ('bathroom-1f', 'bathroom-1f'),
    ('bathroom-2f', 'bathroom-2f'),
)
BEDROOM = 'bedroom'
OFFICE = 'office'
OFFICE_COMFORT = Decimal('21.5')
OFFICE_SETBACK = Decimal('19.0')
BEDROOM_COMFORT_AT_LEAST = Decimal('20.5')
BEDROOM_SETBACK_AT_LEAST = Decimal('19.5')
BACKUP_MARGIN = Decimal('0.5')
GUARD_THRESHOLD = Decimal('19.0')
UNEQUAL_OPEN_SPACE = (
    'site_barsoom 0009: the open-space zones living-room, kitchen and hallway differ in comfort windows '
    'or air targets; make them equal in the panel, then migrate again'
)
MISSING_SOURCE = (
    'site_barsoom 0009: the room %(room)s holds the zones %(zones)s but no zone %(zone)s, whose comfort windows and '
    'air targets it takes; in /admin/climate/zone/ give that zone back the slug %(zone)s and the room %(room)s, or '
    'add it again there, then migrate again'
)
UNRESOLVED_BEDROOM = (
    'site_barsoom 0009: no room has the slug bedroom, whose backup heat goes back into the climate settings as the '
    'second-floor guard, while a room climate names an air sensor or a backup zone; give the bedroom back the slug '
    'bedroom in /admin/house/room/, then roll back again'
)


def move_comfort(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    climate_model = apps.get_model('climate', 'RoomClimate')
    rooms = {room.slug: room for room in apps.get_model('house', 'Room').objects.all()}
    zones = {zone.slug: zone for zone in zone_model.objects.all()}
    _check_sources(rooms, zones)
    open_space = [zones[slug] for slug in OPEN_SPACE_ZONES if slug in zones]
    if len({_comfort(zone, period_model) for zone in open_space}) > 1:
        raise RuntimeError(UNEQUAL_OPEN_SPACE)

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
        period_model.objects.bulk_create(
            [
                period_model(room_climate=climate, zone=None, weekday=period.weekday, start=period.start, end=period.end)
                for period in period_model.objects.filter(zone=zone)
            ],
        )
    if BEDROOM in rooms:
        climate_model.objects.create(room=rooms[BEDROOM], **_bedroom(apps))
    if OFFICE in rooms:
        climate_model.objects.create(room=rooms[OFFICE], comfort_target=OFFICE_COMFORT, setback_target=OFFICE_SETBACK)


def restore_comfort(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    zone_model = apps.get_model('climate', 'Zone')
    period_model = apps.get_model('climate', 'ComfortPeriod')
    climate_model = apps.get_model('climate', 'RoomClimate')
    apps.get_model('climate', 'ClimateSettings').objects.update(**_guard(climate_model))
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


def _comfort(zone: Model, period_model: type[Model]) -> tuple[frozenset[tuple[object, ...]], Decimal, Decimal]:
    windows = frozenset(period_model.objects.filter(zone=zone).values_list('weekday', 'start', 'end'))
    return windows, zone.comfort_air_target, zone.night_air_target


def _check_sources(rooms: dict[str, Model], zones: dict[str, Model]) -> None:
    for room_slug, zone_slug in ROOM_ZONES:
        room = rooms.get(room_slug)
        if room is None or zone_slug in zones:
            continue

        held = sorted(zone.slug for zone in zones.values() if zone.room_id == room.pk)
        if held:
            raise RuntimeError(MISSING_SOURCE % {'room': room_slug, 'zones': ', '.join(held), 'zone': zone_slug})


def _guard(climate_model: type[Model]) -> dict[str, object]:
    bedroom = climate_model.objects.filter(room__slug=BEDROOM).first()
    if bedroom is not None:
        return {
            'second_floor_sensor_id': bedroom.air_sensor_id,
            'second_floor_guard_zone_id': bedroom.backup_zone_id,
            'second_floor_guard_threshold': bedroom.backup_below,
            'second_floor_guard_hysteresis': bedroom.backup_hysteresis,
            'second_floor_guard_live': bedroom.backup_live,
        }

    if climate_model.objects.exclude(air_sensor=None, backup_zone=None).exists():
        raise RuntimeError(UNRESOLVED_BEDROOM)

    return {'second_floor_sensor_id': None, 'second_floor_guard_zone_id': None}


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
        ('site_barsoom', '0008_house'),
        ('climate', '0010_room_climate'),
    ]

    operations = [migrations.RunPython(move_comfort, restore_comfort)]
