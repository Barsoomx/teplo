from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

PLUGS = ('plug-boiler-1f', 'plug-boiler-2f')
READ_ONLY_DRIVER = 'tuya_plug'
SWITCHABLE_DRIVER = 'tuya_switchable_plug'


def switch_plugs(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('devices', 'Device').objects.filter(
        slug__in=PLUGS,
        driver=READ_ONLY_DRIVER,
        monitor_only=False,
    ).update(driver=SWITCHABLE_DRIVER)


def read_plugs(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('devices', 'Device').objects.filter(slug__in=PLUGS, driver=SWITCHABLE_DRIVER).update(
        driver=READ_ONLY_DRIVER,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0005_loads'),
        ('devices', '0002_switchable_plugs'),
    ]

    operations = [migrations.RunPython(switch_plugs, read_plugs)]
