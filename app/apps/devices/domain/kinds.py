from enum import StrEnum


class DeviceKind(StrEnum):
    THERMOSTAT = 'thermostat'
    PLUG = 'plug'
    TEMPERATURE_SENSOR = 'temperature_sensor'
    PHASE_METER = 'phase_meter'
    CLIMATE_UNIT = 'climate_unit'
    EV_CHARGER = 'ev_charger'


class DeviceDriver(StrEnum):
    TUYA_THERMOSTAT = 'tuya_thermostat'
    TUYA_PLUG = 'tuya_plug'
    ESPHOME_TEMPERATURE = 'esphome_temperature'
    ESPHOME_PHASE_METER = 'esphome_phase_meter'
    UNSUPPORTED = 'unsupported'
