# Deploying teplo to the router

Target: Keenetic/Netcraze with Entware on USB storage, SSH to Entware on port 222.

## First install

1. Copy the router scripts and run the installer:

   ```sh
   scp -P 222 -r deploy/router root@192.168.1.1:/tmp/teplo-router
   ssh -p 222 root@192.168.1.1 sh /tmp/teplo-router/install.sh
   ```

2. Edit `/opt/etc/teplo/teplo.env`: set `DJANGO_SECRET_KEY`, `TEPLO_SITE`, `TEPLO_PANEL_HOST`, `TEPLO_ALLOWED_HOSTS`. Keep `TEPLO_DRY_RUN=true` for the first run.
3. Create `/opt/etc/teplo/secrets.toml` from `deploy/router/secrets.toml.example`, one `[devices.<slug>]` table per device slug of the site, and `chmod 600` it. Local keys come from the Smart Life QR login described in `docs/design.md`.

## Deploy a release

```sh
sh deploy/deploy.sh
```

The script installs pinned requirements into `/opt/share/teplo/venv` (Entware provides `cryptography`), migrates, collects static files, creates or updates the `owner` panel account from `secrets.toml`, switches `/opt/share/teplo/current` and restarts the service. The panel is at `http://192.168.1.1:8088/`.

## Operate

- Service: `/opt/etc/init.d/S90teplo {start|stop|restart|status}`.
- Logs: `/opt/var/log/teplo/teplo.log`; restarts: `/opt/var/log/teplo/restarts.log`.
- The cron watchdog restarts teplo when it stops or exceeds 300 MB RSS and trims the log above 5 MB.
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
