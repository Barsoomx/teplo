from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

FIREPLACE = 'fireplace'
FIREPLACE_ROOM = 'open-space'


def add_fireplace(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    room = apps.get_model('house', 'Room').objects.filter(slug=FIREPLACE_ROOM).first()
    if room is None:
        return

    apps.get_model('climate', 'HeatSource').objects.get_or_create(
        slug=FIREPLACE,
        defaults={'name': 'Камин', 'room': room, 'kind': 'fireplace', 'priority': 1, 'live': False},
    )


def remove_fireplace(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'HeatSource').objects.filter(slug=FIREPLACE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0012_estimated_phase'),
        ('climate', '0012_heat_sources'),
    ]

    operations = [migrations.RunPython(add_fireplace, remove_fireplace)]
