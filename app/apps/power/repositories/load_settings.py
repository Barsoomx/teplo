from collections.abc import Collection
from dataclasses import asdict
from datetime import timedelta

from apps.power.domain.control import LoadControl, PhaseThresholds
from apps.power.domain.load_config import LoadSettingsConfig, LoadSettingsRecord
from apps.power.models import LoadSettings


class LoadSettingsRepository:
    @staticmethod
    def control() -> LoadControl:
        stored = LoadSettings.objects.order_by('pk').first()
        load_settings = LoadSettings() if stored is None else stored
        return LoadControl(
            live=load_settings.load_management_live,
            thresholds=PhaseThresholds(
                start_ceiling=load_settings.start_ceiling_amps,
                shed=load_settings.shed_amps,
                shed_hold=timedelta(seconds=load_settings.shed_hold_seconds),
                restore=load_settings.restore_amps,
                restore_hold=timedelta(seconds=load_settings.restore_hold_seconds),
                shed_interval=timedelta(seconds=load_settings.shed_interval_seconds),
                restore_interval=timedelta(seconds=load_settings.restore_interval_seconds),
                start_interval=timedelta(seconds=load_settings.start_interval_seconds),
                meter_lost_after=timedelta(seconds=load_settings.meter_lost_after_seconds),
            ),
            default_amps=load_settings.default_load_amps,
        )

    @staticmethod
    def config_record() -> LoadSettingsRecord:
        stored = LoadSettings.objects.order_by('pk').first()
        load_settings = LoadSettings() if stored is None else stored
        return LoadSettingsRecord(
            id=None if stored is None else stored.pk,
            config=LoadSettingsConfig(
                start_ceiling_amps=load_settings.start_ceiling_amps,
                shed_amps=load_settings.shed_amps,
                shed_hold_seconds=load_settings.shed_hold_seconds,
                restore_amps=load_settings.restore_amps,
                restore_hold_seconds=load_settings.restore_hold_seconds,
                start_interval_seconds=load_settings.start_interval_seconds,
                meter_lost_after_seconds=load_settings.meter_lost_after_seconds,
                default_load_amps=load_settings.default_load_amps,
            ),
            load_management_live=load_settings.load_management_live,
        )

    @staticmethod
    def save_config(config: LoadSettingsConfig, fields: Collection[str]) -> None:
        values = asdict(config)
        changes = {field: values[field] for field in fields}
        if not changes:
            return

        stored = LoadSettings.objects.order_by('pk').first()
        if stored is None:
            LoadSettings.objects.create(**changes)
            return

        LoadSettings.objects.filter(pk=stored.pk).update(**changes)
