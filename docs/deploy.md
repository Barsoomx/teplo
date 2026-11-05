# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS` and `TEPLO_PANEL_URL` (see [Telegram alerts](#telegram-alerts)). Keep `TEPLO_DRY_RUN=true` for the first run. The installer fills in a random `DJANGO_SECRET_KEY` when the file has none; teplo refuses to start without it.
3. Create `/opt/etc/teplo/secrets.toml` from `deploy/router/secrets.toml.example`, one `[devices.<slug>]` table per Tuya device of the site, and `chmod 600` it. The `barsoom` site has five thermostats, the water heater plugs `plug-boiler-1f` and `plug-boiler-2f` and the office convector `heater-office`. Tuya plugs and the convector need `protocol = '3.5'`: a table without it gets 3.3, the thermostats' protocol, and the plug shows as unavailable. A protocol other than `'3.1'`–`'3.5'`, e.g. `'3,5'`, or an empty `address` shows on the panel as a wrong address or protocol for that device only. A Tuya device without a table stays unavailable and raises a device-lost alert 10 minutes after the start. Local keys come from the Smart Life QR login described in `docs/design.md`. The ESPHome devices, the air and outdoor sensors, the phase meter and the bedroom air conditioner's dongle, take no `[devices.<slug>]` table, as they have no address or key: teplo hears them, and commands the air conditioner, through the broker, so they need only the `[mqtt]` table described in [MQTT broker](#mqtt-broker).
4. For alerts, and the owner's commands from the chat, add the `[telegram]` table described in [Telegram alerts](#telegram-alerts).
5. For the ESPHome sensors, set up the broker and add the `[mqtt]` table as described in [MQTT broker](#mqtt-broker).

## Deploy a release

```sh
sh deploy/deploy.sh
```

The script installs pinned requirements into `/opt/share/teplo/venv` (Entware provides `cryptography`), collects static files and creates or updates the `owner` panel account from `secrets.toml`; only then does it migrate, switch `/opt/share/teplo/current` and restart the service. The panel is at `http://192.168.1.1:8088/`.

```mermaid
flowchart LR
    archive["git archive HEAD<br/>app, deploy"] -->|ssh| release["releases/commit"]
    release --> prepare["pip install --no-deps<br/>collectstatic<br/>owner account"]
    prepare --> migrate["migrate"]
    migrate --> switch["current → releases/commit"]
    switch --> restart["S90teplo restart"]
```

A failed step stops the script before `current` moves, so the running release stays in place. The steps that cannot change the schema come first: `pip install`, `collectstatic`, which opens no database, and the owner account, which writes only Django's user table, there since the first install. So a failure in any of them leaves the running release on the schema it knows. `migrate` comes last, and the switch of `current` and the restart follow it in the same ssh command, so only a failure of `migrate` itself leaves the running release on the new schema for longer than the seconds until the restart.

Between `migrate` and the restart the running release works on the new schema. The release that brings the office convector adds a load of kind `source`, which the releases before it do not know: until the restart, a few seconds, their settings page and the save of the shedding order fail on it, while the dashboard, the heating cycle and load management run on. Open `/settings/` once the new release runs, and never start an older release on that schema before undoing its migrations (see [Roll back](#roll-back)).

The release that brings presence adds the device `pc-office` of kind `presence_sensor`, which the releases before it do not know: until the restart, a few seconds, their dashboard and journal fail on it, and so does their whole alert check, so no device, zone-off or phase alert is raised or cleared, while the heating cycle, the source cycle and load management run on.

A `migrate` that fails stops the script before `current` moves. Each migration is applied whole or not at all, so those before the failing one stay applied, and the running release works on them, as above, until you act: either fix the cause and deploy again, which migrates on from where it stopped, switches `current` and restarts, or undo the migrations it applied as [Roll back](#roll-back) says, with `releases/<commit>` of the failed deploy in place of `current`, as the running release does not know them, then restart the running release with `/opt/etc/init.d/S90teplo restart`.

The release that brings the panel's settings pages adds two checks to the database: every zone's idle floor setpoint below its floor limit, and the climate settings' air start offset at or above the minimum band, 0.20 °C as the migration seeds it. The admin of earlier releases checked neither, so before that deploy look for a value that breaks them:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py shell -c "from decimal import Decimal; from django.db.models import F; from apps.climate.models import ClimateSettings, Zone; print(list(Zone.objects.filter(idle_floor_setpoint__gte=F(\"floor_limit_setpoint\")).values_list(\"slug\", flat=True)), list(ClimateSettings.objects.filter(air_start_offset__lt=Decimal(\"0.20\")).values_list(\"air_start_offset\", flat=True)))"'
```

Its last line reads `[] []` when nothing stands in the way. Otherwise raise the floor limit or lower the idle floor setpoint of the zones it names, or widen the air start offset to 0.20 °C or more, in the admin, then deploy. A deploy that meets such a value anyway stops at `migrate` with `CHECK constraint failed` and the constraint's name, and leaves the running release in place.

After the deploy, send a test alert as shown in [Telegram alerts](#telegram-alerts) to confirm that the new release still reaches the chat.

Layout on the router:

```
/opt/share/teplo/
  releases/<commit>/    app and deploy trees of one commit
  current               symlink to the running release
  venv/                 laid out by hand: Entware's python3 ships no venv module
/opt/etc/teplo/
  teplo.env             settings, read at start
  secrets.toml          device addresses and keys, panel password, Telegram bot, MQTT user (chmod 600)
/opt/etc/init.d/S90teplo
/opt/etc/mosquitto/     broker settings, password file and ACL (chmod 600)
/opt/var/lib/teplo/     SQLite database and static files
/opt/var/log/teplo/     teplo.log, restarts.log
```

Releases ship without tests: `.gitattributes` marks `*_tests.py` and `conftest.py` as `export-ignore`. Django imports every module of a `templatetags` package at startup, and the router has no pytest.

## Telegram alerts

teplo sends its alerts (a lost thermostat, plug or sensor, a stale phase meter, a phase overloaded for a minute, a zone left off while it should heat, teplo back after an outage; see Alerts in `docs/design.md`) to one Telegram chat, set in the `[telegram]` table of `secrets.toml`:

```toml
[telegram]
token = 'bot-token'
chat_id = 'chat-id'
proxy = 'http://proxy.example:3128'
```

- `token`: the bot's token from BotFather.
- `chat_id`: the chat that receives the alerts. Send the bot a message, or add it to a group, and take `chat.id` from `https://api.telegram.org/bot<token>/getUpdates`; a group's id is negative.
- `proxy`: optional, an HTTP proxy for `api.telegram.org`. Leave it out to connect directly.

Without the table, or with an empty `token` or `chat_id`, alerts are only journaled and `teplo.log` says so once. Alerts are written in Russian; `TEPLO_ALERT_LANGUAGE=en` in `teplo.env` switches them to English.

teplo reads the table at start: after adding or changing it, restart teplo (`/opt/etc/init.d/S90teplo restart`). After that, and after every deploy, send a test message:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py send_test_alert'
```

It prints `ok` once Telegram has accepted the message, and otherwise fails with the error code and the reason:

- `telegram_not_configured`: the table is missing, or its `token` or `chat_id` is empty.
- `alert_rejected`: Telegram refused the message. HTTP 401 or 404 means a wrong token; 400 or 403, a wrong `chat_id` or a chat the bot may not write to: open the chat with the bot and press Start.
- `alert_delivery_failed`: Telegram could not be reached. `CERTIFICATE_VERIFY_FAILED` in the reason means the router lacks CA certificates: `opkg install ca-bundle`. A timeout points at the network or the `proxy`. `malformed proxy or API address` means the `proxy` is not a URL like `http://user:password@host:3128`; the reason leaves the value out, as it may carry the proxy's password.

After a power cut the notice that teplo is back asks to mark the cause in the panel and links to it. Set the link in `teplo.env` to the panel's address as a phone on the home network opens it:

```sh
TEPLO_PANEL_URL=http://192.0.2.1:8088/
```

Left empty, the line reads «Отметь причину в панели.» without a link. A notice after a restart of teplo alone has no such line. teplo reads `teplo.env` at start, so restart it after setting the link.

### Telegram commands

The same chat can command teplo: «гости», «гости 2», «гости всё», «холодно в гостиной», «жарко на кухне», «гостиная 22», «гостиная авто», «буст санузел 2», «топлю камин», «камин погас» and «статус»; anything else gets the list of commands (see Commands in `docs/design.md`, section 9). The commands run the panel's use cases and never write to a device, and without the internet the panel still does all they do. They stay off until the `[telegram]` table switches them on:

```toml
[telegram]
token = 'bot-token'
chat_id = 'chat-id'
commands = true
```

Restart teplo after adding the line. `teplo.log` then says `owner_commands_started`, and without the line `owner_commands_disabled`.

- Only messages in `chat_id` count, and each gets one reply at once. Any member of a group chat can command teplo; a private chat with the bot keeps the commands to its owner. In a group the bot sees plain messages only with its privacy mode off (BotFather, `/setprivacy`, Disable) or as an admin of the group.
- Edited messages and messages older than 10 minutes are ignored, so commands sent while teplo or the internet was down are not replayed later. After a power cut teplo reads the chat only once NTP has set the router's clock, which runs hours behind until then; meanwhile `teplo.log` says `owner_commands_wait_for_clock` once. A restart never runs a command twice: each is journaled with its update id before it runs, and teplo resumes after the last one.
- Only one teplo may poll a bot. Telegram answers a second poller of the same token with 409 Conflict, both of them, so never start a second teplo with `commands = true` and the router's token, e.g. on a workstation; `send_test_alert` only sends and is safe.
- While Telegram, the internet or the proxy cannot be reached, `teplo.log` says `owner_commands_poll_failed` with the reason once, and `owner_commands_poll_recovered` when a poll works again; teplo tries every 30 seconds. A 409 Conflict in the reason means another poller uses the bot.
- The journal shows each command as the owner wrote it, «Telegram: «холодно в гостиной»», beside what it changed, e.g. «Гостиная, кухня и прихожая: вручную 23,0° до 18:40» with «Из Telegram».
- The bot finds a room by the word stems of its `aliases` in `/admin/house/room/`, which the site data fills, e.g. «гостин кухн прихож зал» for the open space; add a stem there to name a room another way.

## MQTT broker

The ESPHome sensors (see ESPHome devices in `docs/design.md`) and the office computer publish to Mosquitto on the router, and teplo subscribes there. Entware's `mosquitto-nossl` has no TLS and no `mosquitto_passwd`: it reads its password file as plain text, so the file holds the passwords as written and only the broker may read it. Persistence stays off, as no message is retained and the flash is spared. Run the steps on the router (`ssh -p 222 root@192.168.1.1`).

1. Install the broker and the command-line clients, then add the `mosquitto` account the broker switches to once it has started. The package does not create it; 200 is a user and group id no other account on the router has:

   ```sh
   opkg update
   opkg install mosquitto-nossl mosquitto-client-nossl
   grep -q '^mosquitto:' /opt/etc/group || echo 'mosquitto:x:200:' >> /opt/etc/group
   grep -q '^mosquitto:' /opt/etc/passwd || echo 'mosquitto:*:200:200:mosquitto:/opt/var/run:/opt/bin/nologin' >> /opt/etc/passwd
   ```

2. Replace `/opt/etc/mosquitto/mosquitto.conf` with a listener on the LAN address only, so the broker is not reachable from the internet side:

   ```sh
   cat > /opt/etc/mosquitto/mosquitto.conf <<'EOF'
   listener 1883 192.168.1.1
   allow_anonymous false
   password_file /opt/etc/mosquitto/passwd
   persistence false
   EOF
   ```

3. Create the password file with three users, one `user:password` line each: `esphome` for the nodes, `teplo` for teplo and `pc-office` for the office computer. The broker drops to the `mosquitto` user before it reads the file:

   ```sh
   cat > /opt/etc/mosquitto/passwd <<'EOF'
   esphome:choose-a-node-password
   teplo:choose-a-teplo-password
   pc-office:choose-a-pc-password
   EOF
   chown mosquitto:mosquitto /opt/etc/mosquitto/passwd
   chmod 600 /opt/etc/mosquitto/passwd
   ```

   The nodes take the broker address, `esphome` and its password from `esphome/secrets.yaml`: `mqtt_broker`, `mqtt_username`, `mqtt_password`. The office computer takes `pc-office` and its password from `pc_presence.toml` beside its script (see [The office computer](#the-office-computer)).

4. Start the broker. Its init script, `/opt/etc/init.d/S80mosquitto`, also starts it at boot, before `S90teplo`:

   ```sh
   /opt/etc/init.d/S80mosquitto restart
   ```

5. Give teplo its user in `/opt/etc/teplo/secrets.toml` and restart teplo (`/opt/etc/init.d/S90teplo restart`):

   ```toml
   [mqtt]
   host = '192.168.1.1'
   port = 1883
   username = 'teplo'
   password = 'choose-a-teplo-password'
   ```

   `port` may be left out for 1883. Without the table, or with an empty `host`, `username` or `password`, teplo runs without the sensors, and each of them raises a device-lost alert 10 minutes after the start.

6. Check that readings arrive:

   ```sh
   mosquitto_sub -h 192.168.1.1 -i teplo-check -u teplo -P 'choose-a-teplo-password' -t 'teplo/#' -v
   ```

   `-i` names the client. The broker on the router cannot make up a client id, so it drops a client that sends none: the client says `The connection was lost` and the broker logs `Client <unknown> disconnected due to out of memory`. The nodes and teplo always send theirs.

   Every node that is up prints its readings within 30 seconds, e.g. `teplo/teplo-air-kitchen/sensor/temperature/state 21.5`: the air and outdoor sensors every 30 seconds, the phase meter every second. No message is retained, so a node that is already connected prints no status line: `teplo/<node>/status online` appears only when a node connects, and `offline` when it drops. Stop with Ctrl-C.

   The dashboard shows a reading within 10 seconds of its arrival: an air sensor in its zone's tile or its room's card, or in the row under the house facts when it belongs to no room, the outdoor temperature among the house facts, and the phase bars below the loads, refreshed every 3 seconds. `api/v1/sensors/` and `api/v1/phases/` give the same as JSON.

7. Confine each user to its kind of topics with an ACL file, which the broker reads at start, and restart the broker:

   ```sh
   cat > /opt/etc/mosquitto/acl <<'EOF'
   user teplo
   topic read teplo/#
   topic write teplo/+/climate/+/+/command

   user esphome
   topic write teplo/+/status
   topic write teplo/+/sensor/+/state
   topic write teplo/+/climate/+/+/state
   topic read teplo/+/+/+/command
   topic read teplo/+/climate/+/+/command
   topic write esphome/discover/#
   topic read esphome/discover
   topic read esphome/ping/#

   user pc-office
   topic write teplo/teplo-pc-office/#
   EOF
   chown mosquitto:mosquitto /opt/etc/mosquitto/acl
   chmod 600 /opt/etc/mosquitto/acl
   grep -q '^acl_file ' /opt/etc/mosquitto/mosquitto.conf || echo 'acl_file /opt/etc/mosquitto/acl' >> /opt/etc/mosquitto/mosquitto.conf
   /opt/etc/init.d/S80mosquitto restart
   ```

   teplo and the nodes reconnect on their own within seconds; meanwhile the sensors record `broker_unavailable`, and the journal may show the office computer «нет связи» until its next report. What each line allows:

   - `teplo` subscribes to `teplo/+/sensor/+/state`, `teplo/+/status` and `teplo/+/climate/+/+/state`, which `teplo/#` covers, and alone publishes commands: the mode and target of an air conditioner on `teplo/<node>/climate/<id>/<attribute>/command`.
   - Every node publishes its birth, will and shutdown messages on `teplo/<node>/status` and its readings on `teplo/<node>/sensor/<id>/state`, the state topics set in `esphome/common/air.yaml`, `diagnostics.yaml` and `radar.yaml` and the phase meter's. The air conditioner and IR nodes publish their climate states and read their climate and button command topics.
   - ESPHome's IP discovery, on by default, makes every node read `esphome/discover` and `esphome/ping/<node>` and answer on `esphome/discover/<node>`.
   - The office computer publishes only under its node, `teplo/teplo-pc-office/`, its will included. A computer that reports for another room needs a line of its own here, as the broker drops the reports of a node the ACL does not name.
   - The nodes share the user `esphome`, whose password sits in `esphome/secrets.yaml` and in every node's flash, so whoever holds it can publish readings, climate states and status as any node, the office computer and the air conditioner included: the ACL confines only the commands and the computer's own user.
   - The broker drops any other publish without a word to the client, such as the state of a radar's Bluetooth switch, which teplo does not read; the broker run with `-v` in the foreground logs each as `Denied PUBLISH`. The check of step 6, `mosquitto_sub -u teplo -t 'teplo/#'`, keeps working.

   Check the ACL on a node name teplo does not know, `teplo-acl-check`, so that no probe reaches a device or its readings. Watch it in one shell:

   ```sh
   mosquitto_sub -h 192.168.1.1 -i acl-watch -u teplo -P 'choose-a-teplo-password' -t 'teplo/teplo-acl-check/#' -v
   ```

   and publish from another:

   ```sh
   mosquitto_pub -h 192.168.1.1 -i acl-check -u esphome -P 'choose-a-node-password' -t teplo/teplo-acl-check/sensor/temperature/state -m 1
   mosquitto_pub -h 192.168.1.1 -i acl-check -u pc-office -P 'choose-a-pc-password' -t teplo/teplo-acl-check/sensor/temperature/state -m 1
   mosquitto_pub -h 192.168.1.1 -i acl-check -u esphome -P 'choose-a-node-password' -t teplo/teplo-acl-check/climate/probe/mode/command -m off
   ```

   The first prints `teplo/teplo-acl-check/sensor/temperature/state 1` in the watching shell, and teplo logs `esphome_node_unknown` for `teplo-acl-check` once: the nodes' user may publish readings. The second and the third print nothing, as the computer's user publishes only under its own node and only teplo publishes commands, although `mosquitto_pub` succeeds, since the broker drops a refused publish without telling the client. Last, step 6 on `teplo/#` still shows the real nodes' readings.

   A broker set up before block C has neither the `pc-office` user nor the ACL. Add the user's line to the password file, then take this step whole:

   ```sh
   grep -q '^pc-office:' /opt/etc/mosquitto/passwd || echo 'pc-office:choose-a-pc-password' >> /opt/etc/mosquitto/passwd
   ```

`teplo.log` says `mqtt_connected` once teplo has subscribed. `mqtt_connection_refused` means a wrong `username` or `password` in `[mqtt]`; `mqtt_connect_failed` means the broker is down or listens on another address. teplo keeps retrying on its own, 1 second after a failure and then up to every 30 seconds. `esphome_node_unknown` names a node that publishes but matches no teplo device, e.g. a misspelt node name, and `mqtt_not_configured` a missing `[mqtt]` table. The broker logs to standard error, which the init script discards; to watch it, stop the service and run `/opt/sbin/mosquitto -c /opt/etc/mosquitto/mosquitto.conf -v` in the foreground.

## ESPHome nodes

The sensor nodes run ESPHome firmware built from `esphome/`: one configuration per node on top of the shared packages in `esphome/common/`.

The six ESP32-C3 SuperMini boards are wired alike, so the spare replaces any of them. 3.3 V feeds the sensors and 5 V only the IR LED. GPIO0 is SDA and GPIO1 SCL of the SHT30 at 0x44; GPIO10 is the DS18B20's 1-Wire line, pulled up by 4.7 kΩ; GPIO3 drives the IR LED through 4.7 kΩ into an S8050 base, the LED fed from 5 V through 100 Ω; GPIO4 reads the VS1838B receiver; GPIO8 is the on-board status LED. The phase meter is an ESP32-S3-DevKitC-1 with a U.FL antenna led outside the shed's metal door, which a PCB antenna does not get through; the board package `common/s3.yaml` leaves the transmit power at its maximum and has no status LED. Its octal PSRAM takes GPIO35–37, which have no ADC either, so no clamp goes there. The clamps L1, L2 and L3 are on GPIO6, GPIO5 and GPIO4. L1 sits on the stabilizer feeding the living-room and hallway floors, L3 on the one feeding the kitchen and second-floor bathroom floors and the first-floor water heater: switching each floor on in turn shows its step on its phase's clamp. The first-floor bathroom floor showed on no clamp, so it is on the phase without a stabilizer, with the second-floor water heater; that phase has no clamp yet, and the L2 clamp is kept for it. The second-floor air node is an ESP32 DevKit (`common/devkit.yaml`), whose PCB antenna at full power reaches the router from there, with the SHT30 on GPIO21 (SDA) and GPIO22 (SCL). It and the office node carry an HLK-LD2410C radar (`common/radar.yaml`) on UART2: the radar's TX on GPIO16, its RX on GPIO17, VCC on VIN (5 V), OUT unused. Only `presence` reaches MQTT: 1 when the radar saw a person in the last 30 s, 0 when it saw nobody, `nan` when it has sent no frame for 10 s; the node turns the radar's Bluetooth off at boot. The bedroom AC is driven by `teplo-ac-bedroom`, a Seeed XIAO ESP32C3 on a USB-A plug in the unit's Wi-Fi slot. The plug's pin 2 (D−) is the unit's 5 V TX, divided by 4.7 kΩ over 10 kΩ into GPIO20; GPIO21 drives an S8050 whose collector, pulled up by 10 kΩ to the slot's 5 V, feeds pin 3 (D+) through 1 kΩ, so the UART's TX is inverted; a Schottky diode passes the slot's 5 V to the board. The board never takes a USB-C cable while it sits in the unit.

| Node | Board | Publishes |
|---|---|---|
| `teplo-air-living-room`, `teplo-air-kitchen`, `teplo-air-hallway` | ESP32-C3 SuperMini, SHT30 | `temperature`, `humidity` every 30 s |
| `teplo-air-second-floor` | ESP32 DevKit, SHT30, LD2410C radar | `temperature`, `humidity` every 30 s; `presence` every 30 s |
| `teplo-air-office` | ESP32 DevKit, SHT30, LD2410C radar | `temperature`, `humidity` every 30 s; `presence` every 30 s |
| `teplo-outdoor` | ESP32-C3 SuperMini, DS18B20 | `temperature` every 30 s |
| `teplo-phase-meter` | ESP32-S3-DevKitC-1 with U.FL antenna, three SCT-013-050 clamps | `l1_current`, `l2_current`, `l3_current` every second |
| `teplo-ac-bedroom` | Seeed XIAO ESP32C3 in the AC's USB Wi-Fi slot | the AC over the Midea UART protocol: its whole `climate` state every 30 s and at each change, `outdoor_temperature`, `power_usage` |
| `teplo-ir-bedroom` | ESP32-C3 SuperMini, IR LED and receiver | air conditioner control by IR; not a teplo device, which commands the unit through `teplo-ac-bedroom` only |
| `teplo-spare` | ESP32-C3 SuperMini | the same as an air node; without a sensor it publishes `nan` |

Each node also publishes `wifi_signal` and `uptime`. A reading goes to the router's Mosquitto broker, not retained, on `teplo/<node>/sensor/<reading>/state`, as a decimal or `nan` when the read failed; `teplo/<node>/status` carries `online` and `offline`. `teplo-ac-bedroom` publishes the unit's `mode`, `target_temperature` and `current_temperature` on `teplo/teplo-ac-bedroom/climate/air_conditioner/<attribute>/state` and takes teplo's commands on `…/mode/command` and `…/target_temperature/command`; 5 s after a call that switches the unit from off to heat it turns the unit's display off, when the unit reports light control. A node keeps measuring while the broker is down and reconnects when it returns. The nodes have no ESPHome native API. teplo hears no radar yet: the radars' `presence` reaches it once each radar is added as a presence source with its node (see [Operate](#operate)).

A sensor that fails when its node starts also yields `nan`. An SHT30 that does not answer at boot stays out of use until the node restarts: the node publishes `nan` for it every 30 s, and once the wiring is fixed, the node needs a power cycle. While the outdoor node has found no DS18B20, it searches its 1-Wire line every 30 s and publishes `nan` until the sensor answers.

1. Copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` and fill it in: the home Wi-Fi network in `wifi_ssid` and `wifi_password`; new passwords in `fallback_ap_password` (8 characters or more), `ota_password` and `web_password`, with `web_username` for the IR node's page; the router's LAN address in `mqtt_broker`; the nodes' Mosquitto account in `mqtt_username` and `mqtt_password`. Git ignores `esphome/secrets.yaml` and `esphome/.esphome/`, so the values stay on the machine that builds.

2. Compile every node, or the ones named:

   ```sh
   sh scripts/esphome.sh compile
   sh scripts/esphome.sh compile teplo-air-kitchen
   ```

   The script builds in Docker with the ESPHome image it pins, keeps the toolchains and build trees in the Docker volumes `teplo-esphome-cache` and `teplo-esphome-build`, and copies each firmware to `esphome/.esphome/firmware/<node>.factory.bin`, printing the path. The first build downloads the ESP-IDF toolchains; later builds reuse them. `ESPHOME_JOBS` caps the parallel compile jobs, 3 by default. `sh scripts/esphome.sh config` validates the configurations without building. Under WSL with Docker Desktop, point the script at the Windows binary and at the Windows path of the checkout:

   ```sh
   export DOCKER='/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe'
   export MOUNT_SOURCE="$(wslpath -w .)"
   ```

3. Flash a new board over USB the first time. Connect it with the 2 m data cable (a charge-only cable shows no port), open https://web.esphome.io in Chrome and press CONNECT. Pick `USB JTAG/serial debug unit` for a SuperMini, or for an S3 DevKitC plugged into its port marked USB (not UART), and `USB-SERIAL CH340` for the ESP32 DevKit; press INSTALL, choose the node's `.factory.bin` and agree to erase the board. No port, or the upload hangs at `Connecting…`: unplug a SuperMini or an S3 and plug it back in with BOOT held; hold the ESP32 DevKit's BOOT button for 2–3 s. LOGS then shows the node joining Wi-Fi and its first readings.

4. Check each node at `http://<node>/`, e.g. `http://teplo-air-kitchen/`, or at `http://<node>.local/` or the address in the router's client list. The node serves the page itself, without files from the internet; it lists the readings and the log. A node that cannot join Wi-Fi for 90 s opens an access point named after itself, with `fallback_ap_password`; a phone that joins it gets a page that takes new Wi-Fi credentials.

5. Update a node over the air from then on, by its address:

   ```sh
   sh scripts/esphome.sh upload teplo-air-kitchen 192.0.2.21
   ```

   `upload` builds the node first and authenticates with `ota_password`. Docker Desktop's containers do not reach the LAN, so with `DOCKER` set to `docker.exe` the script forwards the node's OTA port 3232 from WSL with `scripts/ota_forward.py` and uploads to `host.docker.internal`; WSL needs `python3`. A node on a weak link may drop the upload: run it again, or bring the node nearer the router for the minute it takes.

6. Calibrate the phase meter with a kettle once the clamps sit on the phase conductors. The firmware starts from the clamps' nominal 50 A per volt (`multiply: 50`). The clamps sit on the input conductors, before the stabilizers, so on L1 and L2 a clamp carries the stabilizer's input current, which runs above the current of the load behind it, the more so the lower the mains voltage. Calibrate on L3, which has no stabilizer yet.

   1. Plug the kettle through a metered plug into an outlet on L3: with the kettle on, L3 is the phase that rises on `http://teplo-phase-meter/`.
   2. Quiet L3 until it reads 0.3 A or less: switch off what runs on it and, if the fridge is on L3, wait for its compressor to stop. The idle current of a fridge or of power supplies is mostly out of phase with the kettle's, so it does not simply add to it: `I_on − I_off` can fall short of the kettle's current by up to `I_off`, which at 0.3 A is under 4 % of a 2 kW kettle's.
   3. Note L3 just before the kettle starts, `I_off`, and while it boils, `I_on`, together with the plug's power `P` and voltage `U` from the panel or the Smart Life app. The current the kettle adds at the clamp is then its own, `I_k = P / U`. Take both from the plug, not from the label: a kettle's power follows the square of the voltage.
   4. The clamps' factor is `50 × I_k / (I_on − I_off)`. Between 48 and 52, keep `multiply: 50`: a kettle measures no closer than that. Otherwise, in `esphome/teplo-phase-meter.yaml`, replace `- multiply: 50` on all three clamps with a line from 0 to the kettle: `(I_on − I_off) / 50` is the RMS voltage the kettle adds at the clamp, `I_k` its current. For a reading that rose from 0.20 A to 9.40 A while the plug showed 1960 W at 228 V, so 8.60 A, the factor is 46.7:

      ```yaml
      - calibrate_linear:
          - 0.0 -> 0.0
          - 0.184 -> 8.60
      ```

   5. Upload the phase meter, switch the kettle on again and check that L3 rises by `I_k`.

   The three clamps are one model on one ADC, so the factor from L3 serves all three. Without an outlet on L3, take the same steps on L1 or L2, where the kettle adds `I_k = P / (0.95 × U_in)` at the clamp: `U_in` is the input voltage on the stabilizer's display while the kettle boils, and 0.95 is the stabilizer's efficiency of about 96 % times its input power factor of 0.99. A factor from L1 or L2 is a few percent less certain than one from L3.

7. Confirm the air conditioner's IR protocol from the receiver dump, should IR ever be needed. teplo commands the unit through the USB dongle only (see [The bedroom air conditioner](#the-bedroom-air-conditioner)), and `teplo-ir-bedroom` is not a teplo device. It assumes `midea_ir`: the Ballu BSYI-10HN8_V4 is probably a Midea OEM, and its remote has Follow Me. Open `http://teplo-ir-bedroom/`, which asks for `web_username` and `web_password` from `esphome/secrets.yaml` because its page drives the unit, point the original remote at the receiver from 30–50 cm and press On, Heat, 22 °C, Cool and Off, 3 s apart. Each press logs `Received …` lines. `Received Midea` confirms `midea_ir`, and `Received Coolix` calls for the `coolix` platform. Without either, ESPHome does not know the protocol: try the `ballu` platform, or replay the codes captured in the `Received Pronto` lines. Until the dump confirms the protocol, the climate controls on the node's page serve only to test that the unit answers.

To replace a failed SuperMini node, wire the spare like it and flash the spare with that node's firmware; it takes over the node's name and topics.

## The office computer

The office computer is the office's presence source `pc-office`, «Компьютер: кабинет»: `scripts/pc_presence.py` runs in the owner's Windows session, reads when he last used the keyboard or the mouse and publishes it like an ESPHome node, `teplo-pc-office`, through the broker's `pc-office` user (see The office computer in `docs/design.md`, section 4). It needs Python 3.12 from python.org in `C:\Program Files\Python312`, without anything from pip, and the broker's `pc-office` user and ACL (see [MQTT broker](#mqtt-broker), steps 3 and 7).

1. In PowerShell, make a folder for the script and copy into it the script and the example config from a checkout of the repository, the config as `pc_presence.toml`, then set its `password` to the `pc-office` password of the broker's password file:

   ```powershell
   New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\teplo"
   Copy-Item scripts\pc_presence.py "$env:LOCALAPPDATA\teplo\"
   Copy-Item scripts\pc_presence.example.toml "$env:LOCALAPPDATA\teplo\pc_presence.toml"
   notepad "$env:LOCALAPPDATA\teplo\pc_presence.toml"
   ```

   The config stays beside the script, outside the repository, as it holds the password; should the script run from a checkout, Git ignores its `scripts/pc_presence.toml` and its log. `host` is the router and `port` 1883, and `node` stays `teplo-pc-office`, the only node the ACL lets `pc-office` publish under.

2. Before any test in a console, stop the task if it is registered, as one copy runs per Windows session:

   ```powershell
   Stop-ScheduledTask -TaskName 'teplo presence'
   ```

   A second copy exits at once with code 3 and logs `another pc_presence is running in this session`.

3. Test the script in a console:

   ```powershell
   & 'C:\Program Files\Python312\python.exe' "$env:LOCALAPPDATA\teplo\pc_presence.py"
   ```

   It logs to the console and to `pc_presence.log` beside the script: `connected to 192.168.1.1:1883 as pc-office`, then `presence 1`, and `presence 0` or `presence 1` whenever that changes. Ctrl+C publishes `offline` and ends it. `connect failed: not authorized` means a wrong user name or password, and `connect failed: timed out` or a refused connection a broker it cannot reach; it keeps trying, at least every 30 seconds. A config it cannot use ends it at once with code 2 and the reason, e.g. `password must be set`.

4. On the router, watch the reports arrive:

   ```sh
   mosquitto_sub -h 192.168.1.1 -i pc-check -u teplo -P 'choose-a-teplo-password' -t 'teplo/teplo-pc-office/#' -v
   ```

   It shows `teplo/teplo-pc-office/status online` when the script connects, `teplo/teplo-pc-office/sensor/presence/state 1` or `0` every 30 seconds, a `1` within a second of input after a `0`, and `teplo/teplo-pc-office/status offline` after Ctrl+C.

5. Register a task that starts the script at each logon with `pythonw.exe`, which opens no window, and start it now:

   ```powershell
   $action = New-ScheduledTaskAction -Execute 'C:\Program Files\Python312\pythonw.exe' -Argument ('"' + "$env:LOCALAPPDATA\teplo\pc_presence.py" + '"') -WorkingDirectory "$env:LOCALAPPDATA\teplo"
   $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
   Register-ScheduledTask -TaskName 'teplo presence' -Action $action -Trigger $trigger -Settings $settings
   Start-ScheduledTask -TaskName 'teplo presence'
   ```

   The task runs only while the owner is logged on, in his session, which `GetLastInputInfo` needs, as it reads the input of its own session only. It has no time limit, where a task's default stops it after 72 hours, and it goes on when the computer runs on battery. Task Scheduler starts it at a logon and when asked, not again after the script has ended: after a test in a console, start the task again with `Start-ScheduledTask -TaskName 'teplo presence'`.

6. Check in the panel. Within a minute of moving the mouse the «Кабинет» card reads «Занято», still with «Наблюдение»; `/api/v1/presence/` lists `pc-office` with `available` true and `age_seconds` under 30; the journal says «Компьютер: кабинет: на связи» and «Кабинет: занято». After 20 minutes without input the card reads «Никого», and while the computer sleeps or is off «Нет данных» with «Компьютер: кабинет: нет связи».

7. The log, `pc_presence.log` beside the script, rolls over at 1 MB into `pc_presence.log.1` and never holds the password. To remove the script, stop and unregister the task, then delete the folder:

   ```powershell
   Stop-ScheduledTask -TaskName 'teplo presence'
   Unregister-ScheduledTask -TaskName 'teplo presence' -Confirm:$false
   Remove-Item -Recurse "$env:LOCALAPPDATA\teplo"
   ```

## Operate

- Service: `/opt/etc/init.d/S90teplo {start|stop|restart|status}`. `stop` gives teplo 10 s to finish its cleanup and then kills it, so `restart` never runs two instances.
- Logs: `/opt/var/log/teplo/teplo.log`; restarts: `/opt/var/log/teplo/restarts.log`.
- The cron watchdog restarts teplo when its process dies or exceeds 300 MB RSS and trims the log above 5 MB. After `S90teplo stop` it leaves teplo stopped until the next `start`.
- Secrets and settings are read at start: restart after editing them.
- Day to day, the panel's schedule page (`/schedule/`) sets each room's comfort windows and its settings page (`/settings/`) each room's air targets and backup heat, the zones' setpoints, the climate and load thresholds and the shedding order; neither needs a restart. The heating cycle and the load guard pick a change up within a minute, and a thermostat takes a new setpoint or schedule at its zone's next allowed write, at most one change per 30 minutes. Each change is journaled, e.g. «Кухня: пол в комфорте 28° → 27,5°» or «Спальня: воздух в комфорте 20,5° → 21°». The gates `live control`, `air control live`, a room's `backup live` and `follows occupancy`, the fireplace's `live`, a heat source's `live`, `manual hold live`, `load management live` and a load's `live` are switched only in the admin, as described below: the settings page shows each read-only with a link to its admin page, and `TEPLO_DRY_RUN` stays in `teplo.env`.
- Guests and room targets need no gate. «Гости на 1 ночь», «2 ночи» or «3 ночи» in the dashboard's house facts keeps the guest rooms at comfort until 14:00 after the last of those nights, the hour set under Climate on the settings page («Гости до, час»); pressing one while guests stay sets their end anew from now, and «Гости уехали» ends it; a room is a guest room by the checkbox on its card, and the site data makes the open space one. The −/+ of a room whose floors read the air, the open space, or that a heat source teplo commands heats, the office, move its target by 0.5 °C for 2 hours, and «Вернуть расписание» ends that early; the office's target reaches the convector at its next allowed write once its source is live (see [The office convector](#the-office-convector)). Both reach a floor through its usual gates, at the zone's next allowed write within its 30-minute budget: in floor mode a target above the room's own takes each floor that aims lower one setpoint up, night to comfort or comfort to boost, a target below it takes each floor that aims higher to the night setpoint, and a floor already pre-heating for the coming window keeps its pre-heat on a «+». The floor's tile says which setpoint it takes and from when, or that load management holds it off, and the journal lists each change, e.g. «Гостиная, кухня и прихожая: вручную 22,5° до 18:40».
- The floors and rooms come from the site data and are edited in the admin (`/admin/house/floor/`, `/admin/house/room/`). A plug or a sensor that serves no zone takes its room on its own page in `/admin/devices/device/`; a zone's thermostat and air sensor follow the room set on the zone's page in `/admin/climate/zone/`. The site data puts the water heater 1F and washing machine plugs in «Санузел 1 этаж»; if they stand elsewhere, set their room on their pages. The dashboard shows a room once it holds a zone, an air sensor, a plug, a heat source or a presence source, as the office its convector. Each room's comfort, its air sensor, its backup heat and whether it follows occupancy live in `/admin/climate/roomclimate/`, which the site data fills: a room climate takes no air sensor that serves a zone or stands in another room, and the page and the list both refuse `follows occupancy` for a room with a heated floor, «В этой комнате тёплый пол: полы не следуют присутствию».
- Presence sources are devices of kind `presence_sensor` with the driver `esphome_presence`, placed in their room in `/admin/devices/device/`; the site data seeds the office computer, `pc-office`, «Компьютер: кабинет», heard on `teplo-pc-office`, which its script publishes once it runs on the computer (see [The office computer](#the-office-computer)). A source that publishes on another device's node, as a radar on its room's air node, names that device's slug in `node`, e.g. `air-office` for `teplo-air-office`; an empty `node` means its own, `teplo-<slug>`. teplo reads the presence sources when it starts, so restart it after adding one. A presence source never raises the device-lost alert: one that is silent, as a computer that sleeps, only makes its room's occupancy unknown, and the room's card reads «Нет данных» with «Компьютер: кабинет: нет связи» below until the source reports; the journal still says «Компьютер: кабинет: нет связи» about a minute after a start and «… на связи» once it reports. `/api/v1/presence/` lists every presence source with its node, whether it is available and the age of its last report, which shows whether a new source reports. A room follows its occupancy only once its `follows occupancy` is ticked; until then the card shows the occupancy with «Наблюдение», and the journal says «Кабинет: занято», «Кабинет: никого» and what occupancy would give the room's heat sources, «Конвектор: кабинет: по присутствию держал бы 19°» (`docs/design.md`, section 6).

## Enable live control

1. Watch the dry-run journal (`/journal/`) through at least one night and one morning pre-heat.
2. Set `TEPLO_DRY_RUN=false` in `/opt/etc/teplo/teplo.env` and restart.
3. In the admin (`/admin/climate/zone/`), tick `live control` one zone at a time and confirm on the dashboard that the thermostat setpoint follows the target.

### Load management

Load management and the switchable water heater plugs ship in dry-run (`docs/design.md`, sections 4 and 7). Once the phase meter reports, the guard checks the phases every second, learns which phase each load is on and journals the sheds, restores and waiting starts it would make; nothing it decides reaches a plug or a thermostat, and the dashboard's shedding order shows every load with «Наблюдение». Until the clamps are installed the meter never reports and the dry-run guard stays quiet; the phase meter's device-lost alert covers it. Live floors need a reporting meter: once the meter has been silent for 60 seconds, from teplo's start or later, the house runs limp and lets one floor heat per phase (see step 5).

1. Install the clamps and calibrate them (see [ESPHome nodes](#esphome-nodes), step 6), then let the house run for a few days. Each time a water heater switches or a floor's relay turns, the guard looks for the phase whose current stepped with it; three votes for one phase and none for another among the load's last five learn it. Check the learned phases in `/admin/power/load/`, where `learned phase`, `learned amps` and `learning samples` are read-only, on the dashboard, where a load reads «Фаза L2, выучена», or in `api/v1/loads/`. The journal says «Бойлер 1 этаж: выучил фазу L2, 6,2 А» when a phase is learned or its current moves. A floor that stays at its limit never switches, so it learns nothing; a load whose phase stays unknown is shed after the loads of the overloaded phase and counts against every phase when it starts.
2. Set `phase` on a load whose learned phase you have checked, or whose phase you know from the switchboard: the phase you set always wins over the learned one. Set `nominal amps` where you know the current a load adds at the clamp, e.g. from its plug or a kettle test: the water heaters come with 6.0 A, and a load without it counts its learned current, or else the default load current of the load settings, 6.0 A. Set both in the shedding order of the settings page (`/settings/`), where the priorities are edited too, or in the list of loads in the admin.
3. In `barsoom` L2 has no clamp (see [ESPHome nodes](#esphome-nodes)), so the guard estimates it from what it knows draws there: a base of 2.0 A, Water heater 2F's own metered current while its plug is on and Bathroom 1F's current while its floor heats; the site data sets both loads to «Фаза L2, задана». Set Bathroom 1F's `nominal amps` in the shedding order on the settings page, as no clamp lets learning find it, or the estimate counts it at the default 6.0 A. The dashboard draws the L2 bar as a dashed outline with «≈ оценка» under it, and its title lists the parts, e.g. «база 2,0 А · Бойлер 2 этаж 6,4 А · Санузел 1 этаж 6,0 А»; `api/v1/phases/` says `estimated` for it and «статус» in the chat reads «L2 ≈8,4 А». Every entry on L2 says it is an estimate, e.g. «Перегрузка фазы L2 по оценке: 24,6 А», and no phase overload alert comes from it. The estimated phase and its base current are set in `/admin/power/loadsettings/`; once L2 gets its clamp, clear the estimated phase there. Before Water heater 2F and Bathroom 1F go live (step 5), watch the L2 entries for a day or two and check that they match what was on.
4. Watch the journal through a few evenings with the EV charging and the water heaters on. «Перегрузка фазы L2: 26,4 А», «Бойлер 1 этаж: выключил бы розетку» and «Кухня: отложил бы включение» say what load management would do; «Фаза L3: похоже, заряжается электромобиль, +16,0 А» and the badge on the phase bars show what EV detection sees. The thresholds, 20 A start ceiling, 24 A shed threshold and 18 A restore threshold by default, are edited under Load management on the settings page or in `/admin/power/loadsettings/`.
5. Tick `load management live` in `/admin/power/loadsettings/`, then tick `live` in `/admin/power/load/` one load at a time, the water heaters first. A command reaches a device only while `TEPLO_DRY_RUN` is false, and a zone's load also needs `live control` on its zone. The «Наблюдение» chip leaves a load's row once its commands are live; a live shed reads «Отключена» and is journaled as «Бойлер 1 этаж: выключил розетку». A live shed of a zone switches its thermostat off at once, whatever its 30-minute write budget, and the zone returns to its schedule no sooner than 30 minutes later.

   Tick a floor `live` only while the phase meter reports, and only once every floor, live or not, has its phase set or learned. After 60 seconds without its readings the house runs limp: a live floor that heats beside a more important floor of its phase is switched off, journaled as «Кухня: выключил термостат» with «Нет данных от фазометра: по одному полу на фазу». Floors of unknown phase count as one more phase, so a floor whose phase is unknown may heat beside a floor of its real phase; while every phase is unknown, one floor heats in the whole house. A floor switched off this way, or by an overload that had ended before the meter went silent with the phase at or below the restore threshold, comes back while the meter is still silent once no other floor of its phase heats, journaled as «Кухня: вернул термостат расписанию» with «Нет данных от фазометра: на этой фазе не греет другой пол», and its thermostat is switched on at its next allowed write. A floor shed for a phase that was still overloaded when the meter went silent, or a floor an overload shed on a phase that read above the restore threshold then, stays off until the meter reports again and the phase calms, as the car or whatever else loads the phase may still be there. A floor shed for a phase whose clamp still reports, while another clamp is silent, comes back once that phase has stayed calm for 2 minutes with room for it. While the meter is silent, a floor starts, after a restore or at its schedule, only with room for it on a phase whose clamp still reports, and not at all on a phase that went silent overloaded: until that phase reports again, or the floor's `live` is unticked, the start waits, journaled in dry-run as «Кухня: отложил бы включение» with «Фазометр молчит, а фаза замолчала перегруженной». On a phase that went silent without an overload, the floor starts only while the phase's last reading plus the floor's current stays at or below the shed threshold, 24 A by default, however long ago that reading came; otherwise it waits for the phase to report, journaled in dry-run with «Фазометр молчит, а фаза L2 по последнему показанию дошла бы до 24,5 А». A clamp that dies during an overload therefore keeps the floors of its phase that were off then off until it reports again or their `live` is unticked; the stale phase meter alert reports the silent clamp. A floor an overload switched off and the guard handed back also waits for its start while the phase it was switched off for went silent above the restore threshold, as its return would, until it heats again, a restart of teplo included. Unticking a shed floor's `live` hands it back to its schedule at its next allowed write. A water heater stays off until the meter reports again and its phase has stayed calm for 2 minutes with room for it.

Unticking a load's `live`, or `load management live`, releases its live shed within a minute: a plug teplo switched off is switched back on, and a zone returns to its schedule at its next allowed write. Unticking a shed zone's `live control` keeps it shed until its next allowed write, no sooner than 30 minutes after the shed, when teplo switches its thermostat back on. Switching a shed plug back on by hand also ends its shed, journaled as «Бойлер 1 этаж: розетку включили вручную, отключение снято». While a load reads «Отключена», its page in the admin shows its slug, kind, zone and plug read-only, the page of its zone shows the zone's slug and thermostat read-only, and the load cannot be deleted; untick its `live` and wait for the release to change them. teplo remembers the plug or the thermostat each shed switched off, so should a load or a zone get another device while it is shed, as in the minute a locked database delays the shed's record, teplo switches back on the one it switched off, a thermostat at its zone's next allowed write, never the new one.

### Air control

Air control, optimal start and the bedroom's backup heat ship in dry-run (`docs/design.md`, section 6): an air zone with its sensor reads «По воздуху: наблюдение» on the dashboard while floor mode keeps driving its thermostat, and the journal says what air mode would write instead. Switch air control on one zone at a time.

1. Watch the journal through a few nights and mornings. «Прихожая: в режиме по воздуху включил бы обогрев (19,1° при цели 19,5°)» and «… остановил бы обогрев …» mark the would-be starts and stops, at most one per 30 minutes, the zone's write budget. With the defaults heat starts below the target less the start offset, 0.3 °C, and stops at the target less 0.1 °C, 19.2 and 19.4 °C at night: the early-stop offset in effect stays at least `air min band`, 0.2 °C, below `air start offset`, both under Climate on the settings page (`/settings/`) and in `/admin/climate/climatesettings/`, whatever the zone learns, so the would-be state no longer turns every 30 minutes on a band of zero width. Neither page takes a start offset below the band, which would stop heat above the target. Each entry's payload carries `start_offset` and `early_stop_offset` in effect. «Прихожая: наблюдение по воздуху возобновлено, сейчас грел бы …» is the first entry after a restart, a lost air sensor or air control switched off, and not a change; after a lost sensor it compares air mode's target with what floor mode wrote meanwhile, so it shows what live air mode would write on the sensor's return. «Кухня: в режиме по воздуху начал бы разогрев в 04:10, а не в 03:30» shows optimal start and «Санузел 2 этаж: резервный обогрев включил бы комфорт (Спальня 18,8°)» the bedroom's backup heat, which the dashboard shows as «Резерв: Санузел 2 этаж» beside the bedroom's name.
2. Check `/admin/climate/zonelearning/`. A zone learns a morning only from a pre-heat that begins on a trusted clock, with its air and outdoor sensors reading and its thermostat at the night's target, so a night with a power cut, an untrusted clock, a lost sensor or an unreachable thermostat teaches nothing. Optimal start keeps the fixed lead until a zone has 3 learned mornings, and air control may go live before that.
3. Pick a zone that already has `live control` and whose air sensor has stayed on line through the last nights, and tick its `air control live` in `/admin/climate/zone/`. From the next cycle its card reads «По воздуху» and its thermostat follows the floor limit, 30 °C, while the room heats and the idle floor setpoint, 18 °C, while it waits, journaled as «Прихожая: уставка 30°» and «Прихожая: уставка 18°», one change per 30 minutes at most. While its air sensor is lost the zone runs in floor mode, and unticking `air control live` hands its thermostat back to floor mode at its next allowed write.
4. Watch that zone through a night and a morning before the next one. After each stop of heat it learns how far the room coasts: the zone learning list shows the learned early-stop offset beside the one in effect. While the band holds the one in effect below the learned one, the room overshoots its target after a stop; widening `air start offset` gives the learned offset room, at the cost of starting heat lower.
5. Tick the remaining air zones the same way, one at a time, and then `backup live` of the bedroom in `/admin/climate/roomclimate/`.

`/admin/climate/zonelearning/` lists what each zone has learned, read-only: the early-stop offset and the one in effect, the dead time, the heat-up rate of each 5 °C outdoor bucket and the learned mornings.

### The fireplace and learning

The fireplace mark, the cap on a morning's rate and the reset of learning work at once, without a gate; the fire detector ships watching only (`docs/design.md`, section 6).

1. Press «Топлю камин» on the card «Гостиная, кухня и прихожая», or write «топлю камин» in the chat, when the fire is lit: the card reads «Камин топится до 22:10» for 4 hours, and the open space's floors learn neither a morning nor an early stop meanwhile. «Камин погас» on the card or «камин погас» in the chat ends the mark early. Both are journaled, «Камин: топится до 22:10» and «Камин: погас».
2. Compare the detector's entries with your own fires over a few evenings: «Гостиная, кухня и прихожая: похоже, топится камин (+2,1°/ч)» with «Наблюдение: обучение не останавливается» below, and «… похоже, камин прогорел»; while it sees a fire, the card shows «Похоже, топится камин» and «Наблюдение». Once they match the fires lit, tick `live` of «Камин» in `/admin/climate/heatsource/`: from the next cycle a fire the detector sees stops learning as the mark does, and the settings page shows «Детектор камина» as «Работает». Unticking it returns the detector to watching only.
3. A morning faster than 1.5 °C/h, or than three times the rate the zone learned for its outdoor bucket, teaches nothing and reads «Гостиная: утро не выучено: 3,4°/ч быстрее предела 1,5°/ч».
4. To make a zone learn from scratch, as after a morning a fire spoiled, select it in `/admin/climate/zonelearning/` and run «Сбросить обучение»: the page says «Обучение сброшено: Гостиная», the journal keeps the old values, «Гостиная: обучение сброшено» with «Было: утр 4, задержка 35 мин, раннее отключение 0,25°», and the zone runs on its fixed lead until it has learned 3 mornings again.

### Manual changes on the thermostats

A change made with a thermostat's buttons or in the Smart Life app ships watched only (`docs/design.md`, section 6): teplo journals it and still takes the thermostat back at the zone's next allowed write.

1. Watch the journal for a day or two. Each change you make on a thermostat reads «Кухня: изменили на термостате: 28° → 25°, teplo не трогал бы до 18:40», or «… выключили, …» and «… режим hold → smart, …» for the power and the mode. Check that there is one for each change you made, and none after teplo's own writes, «Кухня: уставка 28°», after a shed of load management, or when a thermostat comes back after «нет связи с термостатом». A change nobody made, such as «… выключили, …» after a dip of the power, is a thermostat that rebooted between two polls: with the gate on, it would hold that floor off for 2 hours with the zone-off alert quiet, so leave the gate off while such entries appear.
2. Tick `manual hold live` in `/admin/climate/climatesettings/`, in the list or on the page. From the next poll a change on a thermostat holds its floor for 2 hours, journaled as «… teplo не трогает до 18:40»: teplo writes nothing to it, the floor's tile reads «Изменён на термостате: teplo не трогает до 18:40», the zone-off alert stays quiet and the chat's replies name the hold, and the settings page shows «Ручная правка на термостатах» as «Работает». A change made again during the hold moves its end; a boost ends it early, and a live shed of load management still switches the floor off and ends it.
3. Unticking the gate ends every hold at once: teplo takes each held thermostat back at its zone's next allowed write.

### The office convector

The office convector, a Thermex Frame 2000E, ships watching only (`docs/design.md`, sections 4, 6 and 7): teplo reads it every 30 seconds, journals what it would set and counts it on L2, but nothing reaches it until three gates are on. `TEPLO_DRY_RUN=false` is the fourth.

1. Check that the card «Кабинет» shows the row «Конвектор: кабинет»: on or off, «греет до 35° · High · у прибора 21°» or whatever its app last set, and the chip «Наблюдение»; and that the L2 bar's title counts «Конвектор: кабинет 8,7 А» whenever it is on, as it reports no power and its own temperature reads warm while it heats. The card shows the office's target with − and +, 21,5° in its window 13:00–23:45 and 19° outside it, which the schedule page edits, where the office reads «Конвектор: кабинет держит комфорт с начала окна, без разогрева.». The journal says what teplo would set, «Конвектор: кабинет: поставил бы 19°», and at 13:00 «… поставил бы 22°»; «холодно в офисе» in the chat reads «Кабинет: 21,5° до 18:40.» with «Конвектор: кабинет: 22° — наблюдение, команда не уходит». The shedding order lists it last, «Конвектор: кабинет · Фаза L2, задана · 8,7 А по мощности».
2. Switch load management live for L2 first (see [Load management](#load-management), step 5): tick `load management live` in `/admin/power/loadsettings/` if it is not yet, then `live` of «Конвектор: кабинет» in `/admin/power/load/`. 2 kW on a phase without a clamp never runs under teplo without them.
3. Tick `live` of «Конвектор: кабинет» in `/admin/climate/heatsource/`; the list's column «Команды» names any gate still missing. Once the load guard has read the gates, within a minute, teplo sets the convector's own setpoint, 19 °C outside the office's window and 22 °C in it or after «холодно в офисе», never the 35 °C of its app, so a teplo that is down leaves it at the last of those. A command that switches the convector on switches it on and gives it its setpoint at once, «Конвектор: кабинет: включил на 22°», as it ignores a setpoint while it is off; one it does not take switches it off again, journaled as «… не удалось включить на 22°», and teplo tries again at its next allowed write. Each command waits for the convector's interval, 5 minutes, journaled as «Конвектор: кабинет: поставил 22°». A change made with the convector's own buttons or in Smart Life holds it for 2 hours, «Конвектор: кабинет: изменили на приборе: 22° → 25°, teplo не трогает до 18:40», and the row reads «Вручную до 18:40». An overload of L2 switches it off, «Конвектор: кабинет: выключил обогреватель», and teplo starts it again once the phase calms, with a start permit.

The bias added to the target, the interval between commands, the highest setpoint and the rated power are edited on the source's page in `/admin/climate/heatsource/`. Unticking the source's `live` stops the commands at once; teplo leaves the convector as it was, and a shed one goes back on at its next allowed write.

### Presence in the office

The office's occupancy is shown and journaled from the moment the office computer reports (see [The office computer](#the-office-computer)) and drives nothing until the office's `follows occupancy` is ticked (`docs/design.md`, section 6, Occupancy).

1. Watch the journal for a few days: «Кабинет: никого» about 20 minutes after the last input, «Кабинет: занято» at the next one, «Компьютер: кабинет: нет связи» and «Кабинет: нет данных о присутствии» while the computer sleeps or is off, and «Конвектор: кабинет: по присутствию держал бы 19°» or «… 22°» whenever occupancy would give the convector another setpoint than the office's windows do.
2. Before ticking, choose what a computer that sleeps or is off should mean. With the gate on, an office without presence data follows its windows: inside 13:00–23:45 it goes back to 21.5 °C, the convector to 22 °C, with nobody there, and a computer that sleeps sooner than `vacant after minutes` never reads «Никого», as it sleeps before the 20 minutes without input are up. Choose one:
   - set the computer's sleep timer longer than `vacant after minutes`, or to never, during working hours;
   - lower `vacant after minutes` below the sleep timer;
   - delete the office's comfort windows on the schedule page, so that an office without presence data holds 19 °C and only presence brings comfort;
   - accept the reheat.
3. Tick `follows occupancy` of «Кабинет» in `/admin/climate/roomclimate/`, in the list's column or on its page, and leave `vacant after minutes` at 20 unless you chose otherwise. From the next minute the office's target reads 19,0° while nobody is there and its comfort target while someone is, the chip loses «Наблюдение», and the convector takes the target at its next allowed write, at most 5 minutes later: 19 °C some 20 to 25 minutes after the office empties and 22 °C within about 5 minutes of the return. The convector needs nothing more once its own gates are on (see [The office convector](#the-office-convector)).
4. −/+ on the card and «холодно в офисе» still win for 2 hours; the card's button and «кабинет авто» in the chat then read «Снова по присутствию» and end that early. Unticking the gate returns the office to its windows.

### The bedroom air conditioner

teplo ships the driver of the bedroom's Ballu, `esphome_climate`, and the firmware of its dongle, `teplo-ac-bedroom`, but no unit: the site data seeds none, as a device that never reports raises the device-lost alert (`docs/design.md`, section 4, ESPHome air conditioner). Add it once the dongle answers, everything watching only, and switch its gates on after a few days of the journal. Load management's `live` is on already.

1. Check the dongle. Flash it on the desk (see [ESPHome nodes](#esphome-nodes)), plug it into the unit and open `http://teplo-ac-bedroom/`, which asks for `web_username` and `web_password`: after the line `MideaDongle:` the log lists the unit's capabilities, light control among them if the unit has it, while `Failed to get 0xB5 capabilities report` and timeouts mean the slot does not answer; the wiring notes say what to swap. Then watch the climate state on the router:

   ```sh
   mosquitto_sub -h 192.168.1.1 -i ac-check -u teplo -P 'choose-a-teplo-password' -t 'teplo/teplo-ac-bedroom/#' -v
   ```

   It shows `teplo/teplo-ac-bedroom/climate/air_conditioner/mode/state`, `…/target_temperature/state` and `…/current_temperature/state` every 30 s, and at once when the remote changes the unit.

2. Find its phase. With the house quiet, switch the unit on in heat with the remote and watch the L1 and L3 bars on the dashboard: the phase whose current steps up is the unit's, and no step on either means L2.
3. In `/admin/devices/device/`, add the device `ac-bedroom`, «Кондиционер: спальня», kind `climate_unit`, driver `esphome_climate`, room «Спальня», `node` empty and `monitor only` unticked, then restart teplo, which reads the ESPHome devices at start. Within a minute the journal says «Кондиционер: спальня: на связи», after «… нет связи» when the first poll came before the node's first state; so it does after each restart.
4. In `/admin/climate/heatsource/`, add the heat source `ac-bedroom`, «Кондиционер: спальня»: kind `air_conditioner`, device `ac-bedroom`, room «Спальня», priority 1, `live` unticked, `min outdoor` −15.0, `min write minutes` 10, `max setpoint` 24.0, `setpoint bias` 0.0, and `rated watts` from the heating input power on the unit's label.
5. In `/admin/power/load/`, add the load `ac-bedroom`, «Кондиционер: спальня»: kind `source`, heat source «Кондиционер: спальня», priority one above the highest taken, the phase found in step 2, `live` unticked.
6. Give the bedroom its first comfort windows on the schedule page, e.g. 21:00–23:45 and 00:00–08:00 every day, as a window cannot cross midnight, and its targets 20,5° and 19,5° on its card of the settings page.
7. Watch the journal for a few days. The card «Спальня» shows the row «Кондиционер: спальня», «греет до 20,5° · у прибора 20°» or «выключен», with «Наблюдение», «Режим владельца» while you cool and «Мороз: на улице -17°» while the outdoor lockout holds it; the bedroom takes −/+ and «холодно в спальне». The journal says what teplo would do, «Кондиционер: спальня: включил бы на 20,5°», and a change made with the remote reads «Кондиционер: спальня: изменили на приборе: режим обогрев → осушение, teplo не трогал бы до 18:40». Then tick the load's `live` in `/admin/power/load/` and the source's `live` in `/admin/climate/heatsource/`, whose column «Команды» names any gate still missing. From then on teplo switches the unit on in heat with its setpoint, «… включил на 20,5°», holds the bedroom for 2 hours after a change made with the remote, takes dry, fan only and auto back to heat after that, and leaves cool to you until you switch the unit off. With the firmware, the display goes dark 5 s after teplo switches the unit on, if the unit reports light control.
8. If the bedroom stays below its target while the unit reads its setpoint, raise `setpoint bias` on the source's page. Backup heat from the floor of Bathroom 2F still takes over below 19,0°.

## Roll back

An older release does not know the migrations of a newer one, so undo them before it runs, from the release you leave and the newest first.

The release you leave runs on while its migrations are undone, and from `migrate devices 0004` on, past presence, its reads fail on the columns they drop: it controls nothing, load management included, and its dashboard, journal and admin pages fail until the restart. So do every admin step of the rollbacks you are going to run first, while it still runs whole: untick the convector's `live` and wait until no load reads «Отключена», untick `load management live` and wait the same way when you go back past load management, and delete what would stop a rollback, the air conditioner's load, heat source and device, presence sources besides the office computer and heaters besides the convector, as each rollback below says. Then run the rollbacks' commands, the switch of `current` and the restart as one ssh command, as the end of this section shows, so the gap lasts seconds, as in a deploy.

Rolling back past the air conditioner driver takes one step, and one check first: delete in the admin the air conditioner's load, heat source and device, `ac-bedroom` and any other with the driver `esphome_climate`, as an older release knows no such driver. While one exists, `migrate devices 0005` stops before it changes anything and names it. Otherwise it drops the driver and puts the constraint on commanding drivers back to the plugs and `tuya_heater`:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate devices 0005'
```

Rolling back past presence takes one step. `migrate devices 0004` undoes the site's `0015_office_presence` first, which deletes the device `pc-office`, then drops the devices' `node` and the presence kind and driver; `migrate climate 0014` then drops the room climates' `follows occupancy` and `vacant after minutes`. A presence source added in the admin besides the office computer stops the rollback before it changes anything and is named: delete it in the admin and run the rollback again. Until both have run, an older release cannot open its dashboard or journal, as it does not know the presence kind:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; manage="/opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py"; $manage migrate devices 0004 && $manage migrate climate 0014'
```

Rolling back past the office convector takes two steps. First untick `live` of «Конвектор: кабинет» in `/admin/climate/heatsource/` and wait until no load on the dashboard reads «Отключена»: an older release never switches the convector back on. A heater added in the admin besides the convector, with its heat source, its load or its device, stops the rollback before it changes anything and is named: delete it in the admin and run the rollback again. Then `migrate climate 0013` undoes the site's `0014_office_convector` first, which deletes the convector's load, its heat source and the device `heater-office` and keeps the office's comfort windows, which an older release reads as the office's schedule and a later deploy finds in place, then the source loads and the heat sources' command fields, and `migrate devices 0003` drops the heater driver and the constraint on commanding drivers. Until both have run, an older release cannot open its settings page or save the shedding order, as it does not know the convector's load:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; manage="/opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py"; $manage migrate climate 0013 && $manage migrate devices 0003'
```

Rolling back past the manual hold takes one step. `migrate climate 0012` drops the gate and the end of each zone's hold; an older release takes a thermostat changed by hand back at the zone's next allowed write, as before:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate climate 0012'
```

Rolling back past the fireplace takes one step. `migrate climate 0011` undoes the site's `0013_fireplace` first, which deletes the fireplace, then drops the heat sources, those added in the admin included, and the zones' reset counters; what the zones learned stays, and an older release learns without the fireplace's mark, the detector or the cap on a morning's rate:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate climate 0011'
```

Rolling back past the L2 estimate takes one step. `migrate power 0006` undoes the site's `0012_estimated_phase` first, which stops estimating L2 and leaves Water heater 2F and Bathroom 1F on `l2`, then drops the estimated phase and its base current; an older release reads L2 from the meter again, the empty input's fraction of an ampere:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate power 0006'
```

Rolling back past the Telegram commands takes one step. `migrate house 0001` undoes the site's `0011_room_aliases` first, then drops the rooms' aliases; an older release ignores `commands = true`:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate house 0001'
```

Rolling back past guests and room overrides takes one step. `migrate climate 0010` undoes the site's `0010_guest_rooms` first, then drops the guest rooms, the overrides and the guests' end and hour:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate climate 0010'
```

Rolling back past the room comfort takes one step, and one check first: in `/admin/house/room/` the room whose backup heat should go back into the second-floor guard must have the slug `bedroom`. If the rollback stops on it after the earlier steps, `migrate house 0002` brings back the column the room admin reads, then rename the room there and roll back again. `migrate climate 0009` undoes the site's `0009_room_comfort` first: it copies each room's comfort windows and air targets back onto its zones, the backup heat of the room with the slug `bedroom` back into the climate settings as the second-floor guard, and deletes the windows only a room held and the room climates; then it undoes the schema. When no room has that slug while a room climate still names an air sensor or a backup zone, as after the bedroom's slug was changed in `/admin/house/room/`, it stops before it changes anything and says so: give the bedroom back the slug `bedroom` there and run it again. With no such room and no room climate naming either, as after the bedroom was deleted, the guard gets no sensor and no zone and stays off:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate climate 0009'
```

Rolling back past the floors and rooms takes one step: undo the site's placement, then the room columns and the floor and room tables. `migrate climate 0008` also undoes the site's `0008_house`, which names «Воздух: спальня» «Воздух: 2 этаж» again, keeps the oven, dishwasher and washing machine plugs and deletes the site's floors with every room on them, rooms added or renamed in the admin included:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; manage="/opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py"; $manage migrate climate 0008 && $manage migrate devices 0002 && $manage migrate house zero'
```

Rolling back past load management takes two steps first:

1. Untick `load management live` in `/admin/power/loadsettings/` and wait until no load on the dashboard reads «Отключена»: an older release never switches a plug back on.
2. From the release you leave, migrate the devices back. This returns the water heater plugs to the read-only driver, which older releases know, and keeps the loads and what they learned for the next deploy:

   ```sh
   ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate devices 0001'
   ```

   A deploy whose `migrate` fails partway leaves `current` on the older release with the newer migrations applied so far: run the same command with `releases/<commit>` of the failed deploy in place of `current`, or deploy again.

Find the release to go back to, then run the commands of every rollback it needs, newest first, with the switch of `current` and the restart at the end of the same ssh command, e.g. back past presence:

```sh
ssh -p 222 root@192.168.1.1 'ls -1t /opt/share/teplo/releases'
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; manage="/opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py"; $manage migrate devices 0005 && $manage migrate devices 0004 && $manage migrate climate 0014 && ln -sfn /opt/share/teplo/releases/<previous> /opt/share/teplo/current && /opt/etc/init.d/S90teplo restart'
```

A rollback that stops anyway, on a row the admin steps missed, ends the command before `current` moves, and the release you leave runs on with the migrations undone so far. Migrate it forward again and restart it, which brings back its control and its admin, delete there what the rollback named, then run the command again:

```sh
ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate && /opt/etc/init.d/S90teplo restart'
```
