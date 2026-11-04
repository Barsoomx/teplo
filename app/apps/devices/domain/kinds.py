from enum import StrEnum


class DeviceKind(StrEnum):
    THERMOSTAT = 'thermostat'
    PLUG = 'plug'
    TEMPERATURE_SENSOR = 'temperature_sensor'
    PHASE_METER = 'phase_meter'
    CLIMATE_UNIT = 'climate_unit'
    PRESENCE_SENSOR = 'presence_sensor'
    EV_CHARGER = 'ev_charger'


class DeviceDriver(StrEnum):
    TUYA_THERMOSTAT = 'tuya_thermostat'
    TUYA_PLUG = 'tuya_plug'
    TUYA_SWITCHABLE_PLUG = 'tuya_switchable_plug'
    TUYA_HEATER = 'tuya_heater'
    ESPHOME_TEMPERATURE = 'esphome_temperature'
    ESPHOME_PHASE_METER = 'esphome_phase_meter'
    ESPHOME_PRESENCE = 'esphome_presence'
    UNSUPPORTED = 'unsupported'


SWITCHABLE_PLUG_DRIVERS = (DeviceDriver.TUYA_SWITCHABLE_PLUG,)
CONTROLLABLE_CLIMATE_UNIT_DRIVERS = (DeviceDriver.TUYA_HEATER,)
COMMANDING_DRIVERS = (*SWITCHABLE_PLUG_DRIVERS, *CONTROLLABLE_CLIMATE_UNIT_DRIVERS)
ESPHOME_DRIVERS = (DeviceDriver.ESPHOME_TEMPERATURE, DeviceDriver.ESPHOME_PHASE_METER, DeviceDriver.ESPHOME_PRESENCE)
