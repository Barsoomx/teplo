from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

PLUGS = (
    ('plug-boiler-1f', 'Бойлер 1 этаж'),
    ('plug-boiler-2f', 'Бойлер 2 этаж'),
)


def create_plugs(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    device_model = apps.get_model('devices', 'Device')
    for slug, name in PLUGS:
        device_model.objects.create(slug=slug, name=name, kind='plug', driver='tuya_plug')


def delete_plugs(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model('devices', 'Device').objects.filter(slug__in=[plug[0] for plug in PLUGS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0001_initial_site'),
    ]

    operations = [migrations.RunPython(create_plugs, delete_plugs)]
