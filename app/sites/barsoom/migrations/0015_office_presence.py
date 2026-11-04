from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import Q

OFFICE = 'office'
COMPUTER = 'pc-office'
COMPUTER_NAME = 'Компьютер: кабинет'


def add_computer(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    office = apps.get_model('house', 'Room').objects.filter(slug=OFFICE).first()
    if office is None:
        return

    apps.get_model('devices', 'Device').objects.get_or_create(
        slug=COMPUTER,
        defaults={'name': COMPUTER_NAME, 'kind': 'presence_sensor', 'driver': 'esphome_presence', 'room': office},
    )


def remove_computer(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    devices = apps.get_model('devices', 'Device').objects
    others = sorted(
        devices.filter(Q(kind='presence_sensor') | Q(driver='esphome_presence'))
        .exclude(slug=COMPUTER)
        .values_list('slug', flat=True),
    )
    if others:
        raise RuntimeError(
            'an older release knows no presence sensor; delete in the admin the presence devices added besides the '
            f'office computer before this rollback: {", ".join(others)}',
        )

    devices.filter(slug=COMPUTER).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('site_barsoom', '0014_office_convector'),
        ('devices', '0005_presence_sensors'),
    ]

    operations = [migrations.RunPython(add_computer, remove_computer)]
