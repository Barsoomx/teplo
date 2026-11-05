#pragma once

#include "esphome/components/midea/air_conditioner.h"

// ESPHome's display_toggle goes over UART only when the unit's capabilities report light control, and over IR
// otherwise. This Ballu reports none, and the dongle has no IR LED, so the toggle goes over UART regardless. A class
// derived from the template base may form a pointer to its protected appliance, which reaches the library's command.
namespace teplo {

using MideaAppliance = esphome::midea::ApplianceBase<dudanov::midea::ac::AirConditioner>;

struct MideaDisplay : MideaAppliance {
  static void toggle(MideaAppliance *appliance) { (appliance->*(&MideaDisplay::base_)).displayToggle(); }
};

}  // namespace teplo
