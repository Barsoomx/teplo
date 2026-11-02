# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS`. Keep `TEPLO_DRY_RUN=true` for the first run. The installer fills in a random `DJANGO_SECRET_KEY` when the file has none; teplo refuses to start without it.
3. Create `/opt/etc/teplo/secrets.toml` from `deploy/router/secrets.toml.example`, one `[devices.<slug>]` table per device slug of the site, and `chmod 600` it. The `barsoom` site has five thermostats and the water heater plugs `plug-boiler-1f` and `plug-boiler-2f`. Tuya plugs need `protocol = '3.5'`: a table without it gets 3.3, the thermostats' protocol, and the plug shows as unavailable. A protocol other than `'3.1'`–`'3.5'`, e.g. `'3,5'`, or an empty `address` shows on the panel as a wrong address or protocol for that device only. A device without a table stays unavailable and raises a device-lost alert 10 minutes after the start. Local keys come from the Smart Life QR login described in `docs/design.md`.
4. For alerts, add the `[telegram]` table described in [Telegram alerts](#telegram-alerts).

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
  secrets.toml          device addresses and keys, panel password, Telegram bot (chmod 600)
/opt/etc/init.d/S90teplo
/opt/var/lib/teplo/     SQLite database and static files
/opt/var/log/teplo/     teplo.log, restarts.log
```

Releases ship without tests: `.gitattributes` marks `*_tests.py` and `conftest.py` as `export-ignore`. Django imports every module of a `templatetags` package at startup, and the router has no pytest.

## Telegram alerts

teplo sends its alerts (a lost thermostat or plug, a zone left off while it should heat, teplo back after an outage; see Alerts in `docs/design.md`) to one Telegram chat, set in the `[telegram]` table of `secrets.toml`:

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

## Operate

- Service: `/opt/etc/init.d/S90teplo {start|stop|restart|status}`. `stop` gives teplo 10 s to finish its cleanup and then kills it, so `restart` never runs two instances.
- Logs: `/opt/var/log/teplo/teplo.log`; restarts: `/opt/var/log/teplo/restarts.log`.
- The cron watchdog restarts teplo when its process dies or exceeds 300 MB RSS and trims the log above 5 MB. After `S90teplo stop` it leaves teplo stopped until the next `start`.
- Secrets and settings are read at start: restart after editing them.

## Enable live control

1. Watch the dry-run journal (`/journal/`) through at least one night and one morning pre-heat.
2. Set `TEPLO_DRY_RUN=false` in `/opt/etc/teplo/teplo.env` and restart.
3. In the admin (`/admin/climate/zone/`), tick `live control` one zone at a time and confirm on the dashboard that the thermostat setpoint follows the target.

## Roll back

```sh
ssh -p 222 root@192.168.1.1 'ls -1t /opt/share/teplo/releases'
ssh -p 222 root@192.168.1.1 'ln -sfn /opt/share/teplo/releases/<previous> /opt/share/teplo/current && /opt/etc/init.d/S90teplo restart'
```

## ESPHome nodes

The sensor nodes run ESPHome firmware built from `esphome/`: one configuration per node on top of the shared packages in `esphome/common/`.

The seven ESP32-C3 SuperMini boards are wired alike, so the spare replaces any of them. 3.3 V feeds the sensors and 5 V only the IR LED. GPIO0 is SDA and GPIO1 SCL of the SHT30 at 0x44; GPIO10 is the DS18B20's 1-Wire line, pulled up by 4.7 kΩ; GPIO3 drives the IR LED through 4.7 kΩ into an S8050 base, the LED fed from 5 V through 100 Ω; GPIO4 reads the VS1838B receiver; GPIO8 is the on-board status LED. The phase meter is an ESP32 DevKit with the clamps L1, L2 and L3 on GPIO34, GPIO35 and GPIO32.

| Node | Board | Publishes |
|---|---|---|
| `teplo-air-living-room`, `teplo-air-kitchen`, `teplo-air-hallway`, `teplo-air-second-floor` | ESP32-C3 SuperMini, SHT30 | `temperature`, `humidity` every 30 s |
| `teplo-outdoor` | ESP32-C3 SuperMini, DS18B20 | `temperature` every 30 s |
| `teplo-phase-meter` | ESP32 DevKit, three SCT-013-050 clamps | `l1_current`, `l2_current`, `l3_current` every second |
| `teplo-ir-bedroom` | ESP32-C3 SuperMini, IR LED and receiver | air conditioner control |
| `teplo-spare` | ESP32-C3 SuperMini | the same as an air node; without a sensor it logs the SHT30 as failed |

Each node also publishes `wifi_signal` and `uptime`. A reading goes to the router's Mosquitto broker, not retained, on `teplo/<node>/sensor/<reading>/state`, as a decimal or `nan` when the read failed; `teplo/<node>/status` carries `online` and `offline`. A node keeps measuring while the broker is down and reconnects when it returns. The nodes have no ESPHome native API.

1. Copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` and fill it in: the home Wi-Fi network in `wifi_ssid` and `wifi_password`; new passwords in `fallback_ap_password` (8 characters or more) and `ota_password`; the router's LAN address in `mqtt_broker`; the nodes' Mosquitto account in `mqtt_username` and `mqtt_password`. Git ignores `esphome/secrets.yaml` and `esphome/.esphome/`, so the values stay on the machine that builds.

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

   `upload` builds the node first and authenticates with `ota_password`.

6. Calibrate the phase meter with a kettle once the clamps sit on the phase conductors. The firmware starts from the clamps' nominal 50 A per volt (`multiply: 50`).

   1. The kettle draws `I_k = P / U`: `P` from its label, `U` the mains voltage, 230 V or what a metered plug shows in the panel. A 2000 W kettle at 230 V draws 8.7 A.
   2. Switch off large loads, open `http://teplo-phase-meter/` and switch the kettle on: the current of its phase rises.
   3. On that phase, note the current just before the kettle starts, `I_off`, and while it boils, `I_on`.
   4. In `esphome/teplo-phase-meter.yaml`, replace `- multiply: 50` on all three clamps with a line from 0 to the kettle: `(I_on − I_off) / 50` is the RMS voltage the kettle adds at the clamp, `I_k` its current. For a reading that rose from 0.90 A to 9.40 A with an 8.7 A kettle:

      ```yaml
      - calibrate_linear:
          - 0.0 -> 0.0
          - 0.170 -> 8.7
      ```

   5. Upload the phase meter and switch the kettle on again: its phase rises by `I_k`.

   The three clamps are one model, so one kettle calibrates all three; for a clamp of its own, repeat with an outlet on that phase.

7. Confirm the air conditioner's IR protocol from the receiver dump before teplo ever drives it. `teplo-ir-bedroom` assumes `midea_ir`: the Ballu BSYI-10HN8_V4 is probably a Midea OEM, and its remote has Follow Me. Open `http://teplo-ir-bedroom/`, point the original remote at the receiver from 30–50 cm and press On, Heat, 22 °C, Cool and Off, 3 s apart. Each press logs `Received …` lines. `Received Midea` confirms `midea_ir`, and `Received Coolix` calls for the `coolix` platform. Without either, ESPHome does not know the protocol: try the `ballu` platform, or replay the codes captured in the `Received Pronto` lines. Until the dump confirms the protocol, the climate controls on the node's page serve only to test that the unit answers.

To replace a failed SuperMini node, wire the spare like it and flash the spare with that node's firmware; it takes over the node's name and topics.
