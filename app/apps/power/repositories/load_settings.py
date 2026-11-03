from datetime import timedelta

from apps.power.domain.control import LoadControl, PhaseThresholds
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
