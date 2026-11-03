from dataclasses import dataclass

from apps.climate.domain.air_policy import AirRegulationSettings
from apps.climate.domain.optimal_start import OptimalStartSettings
from apps.climate.domain.sensors import ClimateSensors


@dataclass(frozen=True, kw_only=True)
class ClimateControl:
    sensors: ClimateSensors
    regulation: AirRegulationSettings
    optimal_start: OptimalStartSettings
