# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS` and `TEPLO_PANEL_URL` (see [Telegram alerts](#telegram-alerts)). Keep `TEPLO_DRY_RUN=true` for the first run. The installer fills in a random `DJANGO_SECRET_KEY` when the file has none; teplo refuses to start without it.
3. Create `/opt/etc/teplo/secrets.toml` from `deploy/router/secrets.toml.example`, one `[devices.<slug>]` table per Tuya device of the site, and `chmod 600` it. The `barsoom` site has five thermostats and the water heater plugs `plug-boiler-1f` and `plug-boiler-2f`. Tuya plugs need `protocol = '3.5'`: a table without it gets 3.3, the thermostats' protocol, and the plug shows as unavailable. A protocol other than `'3.1'`–`'3.5'`, e.g. `'3,5'`, or an empty `address` shows on the panel as a wrong address or protocol for that device only. A Tuya device without a table stays unavailable and raises a device-lost alert 10 minutes after the start. Local keys come from the Smart Life QR login described in `docs/design.md`. The ESPHome devices, the air and outdoor sensors and the phase meter, take no `[devices.<slug>]` table, as they have no address or key: teplo hears them through the broker, so they need only the `[mqtt]` table described in [MQTT broker](#mqtt-broker).
4. For alerts, add the `[telegram]` table described in [Telegram alerts](#telegram-alerts).
5. For the ESPHome sensors, set up the broker and add the `[mqtt]` table as described in [MQTT broker](#mqtt-broker).

## Deploy a release

```sh
sh deploy/deploy.sh
```

The script installs pinned requirements into `/opt/share/teplo/venv` (Entware provides `cryptography`), migrates, collects static files, creates or updates the `owner` panel account from `secrets.toml`, switches `/opt/share/teplo/current` and restarts the service. The panel is at `http://192.168.1.1:8088/`.

```mermaid
flowchart LR
    archive["git archive HEAD<br/>app, deploy"] -->|ssh| release["releases/commit"]
    release --> prepare["pip install --no-deps<br/>migrate, collectstatic<br/>owner account"]
    prepare --> switch["current → releases/commit"]
    switch --> restart["S90teplo restart"]
```

A failed step stops the script before `current` moves, so the running release stays in place.

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
/opt/etc/mosquitto/     broker settings and password file (chmod 600)
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

## MQTT broker

The ESPHome sensors (see ESPHome devices in `docs/design.md`) publish to Mosquitto on the router, and teplo subscribes there. Entware's `mosquitto-nossl` has no TLS and no `mosquitto_passwd`: it reads its password file as plain text, so the file holds the passwords as written and only the broker may read it. Persistence stays off, as no message is retained and the flash is spared. Run the steps on the router (`ssh -p 222 root@192.168.1.1`).

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

3. Create the password file with one user for the nodes and one for teplo, one `user:password` line each. The broker drops to the `mosquitto` user before it reads the file:

   ```sh
   cat > /opt/etc/mosquitto/passwd <<'EOF'
   esphome:choose-a-node-password
   teplo:choose-a-teplo-password
   EOF
   chown mosquitto:mosquitto /opt/etc/mosquitto/passwd
   chmod 600 /opt/etc/mosquitto/passwd
   ```

   The nodes take the broker address, `esphome` and its password from `esphome/secrets.yaml`: `mqtt_broker`, `mqtt_username`, `mqtt_password`.

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

   The dashboard shows a reading within 10 seconds of its arrival: an air sensor in its zone card, or in the row under the house facts when it belongs to no zone, the outdoor temperature among the house facts, and the phase bars below the loads, refreshed every 3 seconds. `api/v1/sensors/` and `api/v1/phases/` give the same as JSON.

`teplo.log` says `mqtt_connected` once teplo has subscribed. `mqtt_connection_refused` means a wrong `username` or `password` in `[mqtt]`; `mqtt_connect_failed` means the broker is down or listens on another address. teplo keeps retrying on its own, 1 second after a failure and then up to every 30 seconds. `esphome_node_unknown` names a node that publishes but matches no teplo device, e.g. a misspelt node name, and `mqtt_not_configured` a missing `[mqtt]` table. The broker logs to standard error, which the init script discards; to watch it, stop the service and run `/opt/sbin/mosquitto -c /opt/etc/mosquitto/mosquitto.conf -v` in the foreground.

## ESPHome nodes

The sensor nodes run ESPHome firmware built from `esphome/`: one configuration per node on top of the shared packages in `esphome/common/`.

The seven ESP32-C3 SuperMini boards are wired alike, so the spare replaces any of them. 3.3 V feeds the sensors and 5 V only the IR LED. GPIO0 is SDA and GPIO1 SCL of the SHT30 at 0x44; GPIO10 is the DS18B20's 1-Wire line, pulled up by 4.7 kΩ; GPIO3 drives the IR LED through 4.7 kΩ into an S8050 base, the LED fed from 5 V through 100 Ω; GPIO4 reads the VS1838B receiver; GPIO8 is the on-board status LED. The phase meter is an ESP32 DevKit with the clamps L1, L2 and L3 on GPIO34, GPIO35 and GPIO32.

| Node | Board | Publishes |
|---|---|---|
| `teplo-air-living-room`, `teplo-air-kitchen`, `teplo-air-hallway`, `teplo-air-second-floor` | ESP32-C3 SuperMini, SHT30 | `temperature`, `humidity` every 30 s |
| `teplo-outdoor` | ESP32-C3 SuperMini, DS18B20 | `temperature` every 30 s |
| `teplo-phase-meter` | ESP32 DevKit, three SCT-013-050 clamps | `l1_current`, `l2_current`, `l3_current` every second |
| `teplo-ir-bedroom` | ESP32-C3 SuperMini, IR LED and receiver | air conditioner control |
| `teplo-spare` | ESP32-C3 SuperMini | the same as an air node; without a sensor it publishes `nan` |

Each node also publishes `wifi_signal` and `uptime`. A reading goes to the router's Mosquitto broker, not retained, on `teplo/<node>/sensor/<reading>/state`, as a decimal or `nan` when the read failed; `teplo/<node>/status` carries `online` and `offline`. A node keeps measuring while the broker is down and reconnects when it returns. The nodes have no ESPHome native API.

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

3. Flash a new board over USB the first time. Connect it with the 2 m data cable (a charge-only cable shows no port), open https://web.esphome.io in Chrome and press CONNECT. Pick `USB JTAG/serial debug unit` for a SuperMini or `USB-SERIAL CH340` for the DevKit, press INSTALL, choose the node's `.factory.bin` and agree to erase the board. No port, or the upload hangs at `Connecting…`: unplug a SuperMini and plug it back in with BOOT held; hold the DevKit's BOOT button for 2–3 s. LOGS then shows the node joining Wi-Fi and its first readings.

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

7. Confirm the air conditioner's IR protocol from the receiver dump before teplo ever drives it. `teplo-ir-bedroom` assumes `midea_ir`: the Ballu BSYI-10HN8_V4 is probably a Midea OEM, and its remote has Follow Me. Open `http://teplo-ir-bedroom/`, which asks for `web_username` and `web_password` from `esphome/secrets.yaml` because its page drives the unit, point the original remote at the receiver from 30–50 cm and press On, Heat, 22 °C, Cool and Off, 3 s apart. Each press logs `Received …` lines. `Received Midea` confirms `midea_ir`, and `Received Coolix` calls for the `coolix` platform. Without either, ESPHome does not know the protocol: try the `ballu` platform, or replay the codes captured in the `Received Pronto` lines. Until the dump confirms the protocol, the climate controls on the node's page serve only to test that the unit answers.

To replace a failed SuperMini node, wire the spare like it and flash the spare with that node's firmware; it takes over the node's name and topics.

## Operate

- Service: `/opt/etc/init.d/S90teplo {start|stop|restart|status}`. `stop` gives teplo 10 s to finish its cleanup and then kills it, so `restart` never runs two instances.
- Logs: `/opt/var/log/teplo/teplo.log`; restarts: `/opt/var/log/teplo/restarts.log`.
- The cron watchdog restarts teplo when its process dies or exceeds 300 MB RSS and trims the log above 5 MB. After `S90teplo stop` it leaves teplo stopped until the next `start`.
- Secrets and settings are read at start: restart after editing them.

## Enable live control

1. Watch the dry-run journal (`/journal/`) through at least one night and one morning pre-heat.
2. Set `TEPLO_DRY_RUN=false` in `/opt/etc/teplo/teplo.env` and restart.
3. In the admin (`/admin/climate/zone/`), tick `live control` one zone at a time and confirm on the dashboard that the thermostat setpoint follows the target.

### Load management

Load management and the switchable water heater plugs ship in dry-run (`docs/design.md`, sections 4 and 7). Once the phase meter reports, the guard checks the phases every second, learns which phase each load is on and journals the sheds, restores and waiting starts it would make; nothing it decides reaches a plug or a thermostat, and the dashboard's shedding order shows every load with «Наблюдение». Until the clamps are installed the meter never reports and the dry-run guard stays quiet; the phase meter's device-lost alert covers it. Live floors need a reporting meter: once the meter has been silent for 60 seconds, from teplo's start or later, the house runs limp and lets one floor heat per phase (see step 4).

1. Install the clamps and calibrate them (see [ESPHome nodes](#esphome-nodes), step 6), then let the house run for a few days. Each time a water heater switches or a floor's relay turns, the guard looks for the phase whose current stepped with it; three votes for one phase and none for another among the load's last five learn it. Check the learned phases in `/admin/power/load/`, where `learned phase`, `learned amps` and `learning samples` are read-only, on the dashboard, where a load reads «Фаза L2, выучена», or in `api/v1/loads/`. The journal says «Бойлер 1 этаж: выучил фазу L2, 6,2 А» when a phase is learned or its current moves. A floor that stays at its limit never switches, so it learns nothing; a load whose phase stays unknown is shed after the loads of the overloaded phase and counts against every phase when it starts.
2. Set `phase` on a load whose learned phase you have checked, or whose phase you know from the switchboard: the phase you set always wins over the learned one. Set `nominal amps` where you know the current a load adds at the clamp, e.g. from its plug or a kettle test: the water heaters come with 6.0 A, and a load without it counts its learned current, or else the default load current of `/admin/power/loadsettings/`, 6.0 A. Both columns can be edited in the list of loads.
3. Watch the journal through a few evenings with the EV charging and the water heaters on. «Перегрузка фазы L2: 26,4 А», «Бойлер 1 этаж: выключил бы розетку» and «Кухня: отложил бы включение» say what load management would do; «Фаза L3: похоже, заряжается электромобиль, +16,0 А» and the badge on the phase bars show what EV detection sees. The thresholds, 20 A start ceiling, 24 A shed threshold and 18 A restore threshold by default, are edited in `/admin/power/loadsettings/`.
4. Tick `load management live` in `/admin/power/loadsettings/`, then tick `live` in `/admin/power/load/` one load at a time, the water heaters first. A command reaches a device only while `TEPLO_DRY_RUN` is false, and a zone's load also needs `live control` on its zone. The «Наблюдение» chip leaves a load's row once its commands are live; a live shed reads «Отключена» and is journaled as «Бойлер 1 этаж: выключил розетку». A live shed of a zone switches its thermostat off at once, whatever its 30-minute write budget, and the zone returns to its schedule no sooner than 30 minutes later.

   Tick a floor `live` only while the phase meter reports, and only once every floor, live or not, has its phase set or learned. After 60 seconds without its readings the house runs limp: a live floor that heats beside a more important floor of its phase is switched off, journaled as «Кухня: выключил термостат» with «Нет данных от фазометра: по одному полу на фазу». Floors of unknown phase count as one more phase, so a floor whose phase is unknown may heat beside a floor of its real phase; while every phase is unknown, one floor heats in the whole house. A floor switched off this way, or by an overload that had ended before the meter went silent with the phase at or below the restore threshold, comes back while the meter is still silent once no other floor of its phase heats, journaled as «Кухня: вернул термостат расписанию» with «Нет данных от фазометра: на этой фазе не греет другой пол», and its thermostat is switched on at its next allowed write. A floor shed for a phase that was still overloaded when the meter went silent, or a floor an overload shed on a phase that read above the restore threshold then, stays off until the meter reports again and the phase calms, as the car or whatever else loads the phase may still be there. A floor shed for a phase whose clamp still reports, while another clamp is silent, comes back once that phase has stayed calm for 2 minutes with room for it. While the meter is silent, a floor starts, after a restore or at its schedule, only with room for it on a phase whose clamp still reports, and not at all on a phase that went silent overloaded: until that phase reports again, or the floor's `live` is unticked, the start waits, journaled in dry-run as «Кухня: отложил бы включение» with «Фазометр молчит, а фаза замолчала перегруженной». On a phase that went silent without an overload, the floor starts only while the phase's last reading plus the floor's current stays at or below the shed threshold, 24 A by default, however long ago that reading came; otherwise it waits for the phase to report, journaled in dry-run with «Фазометр молчит, а фаза L2 по последнему показанию дошла бы до 24,5 А». A clamp that dies during an overload therefore keeps the floors of its phase that were off then off until it reports again or their `live` is unticked; the stale phase meter alert reports the silent clamp. A floor an overload switched off and the guard handed back also waits for its start while the phase it was switched off for went silent above the restore threshold, as its return would, until it heats again, a restart of teplo included. Unticking a shed floor's `live` hands it back to its schedule at its next allowed write. A water heater stays off until the meter reports again and its phase has stayed calm for 2 minutes with room for it.

Unticking a load's `live`, or `load management live`, releases its live shed within a minute: a plug teplo switched off is switched back on, and a zone returns to its schedule at its next allowed write. Unticking a shed zone's `live control` keeps it shed until its next allowed write, no sooner than 30 minutes after the shed, when teplo switches its thermostat back on. Switching a shed plug back on by hand also ends its shed, journaled as «Бойлер 1 этаж: розетку включили вручную, отключение снято». While a load reads «Отключена», its page in the admin shows its slug, kind, zone and plug read-only, the page of its zone shows the zone's slug and thermostat read-only, and the load cannot be deleted; untick its `live` and wait for the release to change them. teplo remembers the plug or the thermostat each shed switched off, so should a load or a zone get another device while it is shed, as in the minute a locked database delays the shed's record, teplo switches back on the one it switched off, a thermostat at its zone's next allowed write, never the new one.

### Air control

Air control, optimal start and the second-floor guard ship in dry-run (`docs/design.md`, section 6): an air zone with its sensor reads «По воздуху: наблюдение» on the dashboard while floor mode keeps driving its thermostat, and the journal says what air mode would write instead. Switch air control on one zone at a time.

1. Watch the journal through a few nights and mornings. «Прихожая: в режиме по воздуху включил бы обогрев (19,1° при цели 19,5°)» and «… остановил бы обогрев …» mark the would-be starts and stops, at most one per 30 minutes, the zone's write budget. With the defaults heat starts below the target less the start offset, 0.3 °C, and stops at the target less 0.1 °C, 19.2 and 19.4 °C at night: the early-stop offset in effect stays at least `air min band`, 0.2 °C, below `air start offset`, both in `/admin/climate/climatesettings/`, whatever the zone learns, so the would-be state no longer turns every 30 minutes on a band of zero width. Each entry's payload carries `start_offset` and `early_stop_offset` in effect. «Прихожая: наблюдение по воздуху возобновлено, сейчас грел бы …» is the first entry after a restart, a lost air sensor or air control switched off, and not a change; after a lost sensor it compares air mode's target with what floor mode wrote meanwhile, so it shows what live air mode would write on the sensor's return. «Кухня: в режиме по воздуху начал бы разогрев в 04:10, а не в 03:30» shows optimal start and «Санузел 2 этаж: защита второго этажа включила бы комфорт …» the guard.
2. Check `/admin/climate/zonelearning/`. A zone learns a morning only from a pre-heat that begins on a trusted clock, with its air and outdoor sensors reading and its thermostat at the night's target, so a night with a power cut, an untrusted clock, a lost sensor or an unreachable thermostat teaches nothing. Optimal start keeps the fixed lead until a zone has 3 learned mornings, and air control may go live before that.
3. Pick a zone that already has `live control` and whose air sensor has stayed on line through the last nights, and tick its `air control live` in `/admin/climate/zone/`. From the next cycle its card reads «По воздуху» and its thermostat follows the floor limit, 30 °C, while the room heats and the idle floor setpoint, 18 °C, while it waits, journaled as «Прихожая: уставка 30°» and «Прихожая: уставка 18°», one change per 30 minutes at most. While its air sensor is lost the zone runs in floor mode, and unticking `air control live` hands its thermostat back to floor mode at its next allowed write.
4. Watch that zone through a night and a morning before the next one. After each stop of heat it learns how far the room coasts: the zone learning list shows the learned early-stop offset beside the one in effect. While the band holds the one in effect below the learned one, the room overshoots its target after a stop; widening `air start offset` gives the learned offset room, at the cost of starting heat lower.
5. Tick the remaining air zones the same way, one at a time, and then `second floor guard live` in `/admin/climate/climatesettings/`.

`/admin/climate/zonelearning/` lists what each zone has learned, read-only: the early-stop offset and the one in effect, the dead time, the heat-up rate of each 5 °C outdoor bucket and the learned mornings.

## Roll back

An older release does not know the migrations of a newer one, so undo them before it runs. Rolling back past load management takes two steps first:

1. Untick `load management live` in `/admin/power/loadsettings/` and wait until no load on the dashboard reads «Отключена»: an older release never switches a plug back on.
2. From the release you leave, migrate the devices back. This returns the water heater plugs to the read-only driver, which older releases know, and keeps the loads and what they learned for the next deploy:

   ```sh
   ssh -p 222 root@192.168.1.1 'set -a; . /opt/etc/teplo/teplo.env; set +a; /opt/share/teplo/venv/bin/python /opt/share/teplo/current/app/manage.py migrate devices 0001'
   ```

   A deploy that fails after its migrate step, e.g. in `collectstatic`, leaves `current` on the older release with the newer migrations applied: run the same command with `releases/<commit>` of the failed deploy in place of `current`, or deploy again.

Then switch `current` back and restart:

```sh
ssh -p 222 root@192.168.1.1 'ls -1t /opt/share/teplo/releases'
ssh -p 222 root@192.168.1.1 'ln -sfn /opt/share/teplo/releases/<previous> /opt/share/teplo/current && /opt/etc/init.d/S90teplo restart'
```
