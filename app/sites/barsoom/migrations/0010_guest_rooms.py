from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

GUEST_ROOMS = ('open-space',)


def mark_guest_rooms(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'RoomClimate').objects.filter(room__slug__in=GUEST_ROOMS).update(guest_room=True)


def unmark_guest_rooms(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'RoomClimate').objects.filter(room__slug__in=GUEST_ROOMS).update(guest_room=False)


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0009_room_comfort'),
        ('climate', '0011_room_intents'),
    ]

    operations = [migrations.RunPython(mark_guest_rooms, unmark_guest_rooms)]
