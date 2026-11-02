# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS`. Keep `TEPLO_DRY_RUN=true` for the first run. The installer fills in a random `DJANGO_SECRET_KEY` when the file has none; teplo refuses to start without it.
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

teplo sends its alerts (a lost thermostat, plug or sensor, a stale phase meter, a zone left off while it should heat, teplo back after an outage; see Alerts in `docs/design.md`) to one Telegram chat, set in the `[telegram]` table of `secrets.toml`:

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

## MQTT broker

The ESPHome sensors (see ESPHome devices in `docs/design.md`) publish to Mosquitto on the router, and teplo subscribes there. Entware's `mosquitto-nossl` has no TLS and no `mosquitto_passwd`: it reads its password file as plain text, so the file holds the passwords as written and only the broker may read it. Persistence stays off, as no message is retained and the flash is spared. Run the steps on the router (`ssh -p 222 root@192.168.1.1`).

1. Install the broker and the command-line clients:

   ```sh
   opkg update
   opkg install mosquitto-nossl mosquitto-client-nossl
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
   mosquitto_sub -h 192.168.1.1 -u teplo -P 'choose-a-teplo-password' -t 'teplo/#' -v
   ```

   Every node that is up prints its readings within 30 seconds, e.g. `teplo/teplo-air-kitchen/sensor/temperature/state 21.5`: the air and outdoor sensors every 30 seconds, the phase meter every second. No message is retained, so a node that is already connected prints no status line: `teplo/<node>/status online` appears only when a node connects, and `offline` when it drops. Stop with Ctrl-C.

   The dashboard shows a reading within 10 seconds of its arrival: an air sensor in its zone card, or in the row under the house facts when it belongs to no zone, the outdoor temperature among the house facts, and the phase bars below the loads, refreshed every 3 seconds. `api/v1/sensors/` and `api/v1/phases/` give the same as JSON.

`teplo.log` says `mqtt_connected` once teplo has subscribed. `mqtt_connection_refused` means a wrong `username` or `password` in `[mqtt]`; `mqtt_connect_failed` means the broker is down or listens on another address. teplo keeps retrying on its own, 1 second after a failure and then up to every 30 seconds. `esphome_node_unknown` names a node that publishes but matches no teplo device, e.g. a misspelt node name, and `mqtt_not_configured` a missing `[mqtt]` table. The broker logs to standard error, which the init script discards; to watch it, stop the service and run `/opt/sbin/mosquitto -c /opt/etc/mosquitto/mosquitto.conf -v` in the foreground.

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
