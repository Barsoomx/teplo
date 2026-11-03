from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

BATHROOM = 'санузел санузл ванн туалет'
ROOM_ALIASES = (
    ('open-space', 'гостин кухн прихож зал'),
    ('bathroom-1f', BATHROOM),
    ('bedroom', 'спальн'),
    ('office', 'кабинет офис'),
    ('bathroom-2f', BATHROOM),
)


def name_rooms(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    room_model = apps.get_model('house', 'Room')
    for slug, aliases in ROOM_ALIASES:
        room_model.objects.filter(slug=slug).update(aliases=aliases)


def unname_rooms(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('house', 'Room').objects.filter(slug__in=[slug for slug, _ in ROOM_ALIASES]).update(aliases='')


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0010_guest_rooms'),
        ('house', '0002_room_aliases'),
    ]

    operations = [migrations.RunPython(name_rooms, unname_rooms)]
