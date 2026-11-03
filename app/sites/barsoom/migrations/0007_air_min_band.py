from decimal import Decimal

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

AIR_MIN_BAND = '0.2'


def seed_air_min_band(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('climate', 'ClimateSettings').objects.update(air_min_band=Decimal(AIR_MIN_BAND))


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0006_switchable_boiler_plugs'),
        ('climate', '0007_air_min_band'),
    ]

    operations = [migrations.RunPython(seed_air_min_band, migrations.RunPython.noop)]
