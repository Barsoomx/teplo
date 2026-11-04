from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

ESTIMATED_PHASE = 'l2'
LOADS_ON_THE_ESTIMATED_PHASE = ('boiler-2f', 'bathroom-1f')


def estimate_l2(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('power', 'LoadSettings').objects.update(estimated_phase=ESTIMATED_PHASE)
    apps.get_model('power', 'Load').objects.filter(slug__in=LOADS_ON_THE_ESTIMATED_PHASE, phase='unknown').update(
        phase=ESTIMATED_PHASE,
    )


def clear_estimated_phase(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('power', 'LoadSettings').objects.update(estimated_phase='')


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0011_room_aliases'),
        ('power', '0007_estimated_phase'),
    ]

    operations = [migrations.RunPython(estimate_l2, clear_estimated_phase)]
