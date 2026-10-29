#!/bin/sh
set -eu

ROOT=/opt/share/teplo
ENV_FILE=/opt/etc/teplo/teplo.env
HERE=$(cd "$(dirname "$0")" && pwd)

opkg update
opkg install python3 python3-pip python3-sqlite3 python3-asyncio python3-cryptography
mkdir -p "$ROOT/releases" /opt/etc/teplo /opt/var/lib/teplo/static /opt/var/log/teplo /opt/var/run
# Entware ships no venv module; a venv is a pyvenv.cfg next to a python symlink, the interpreter does the rest.
if [ ! -x "$ROOT/venv/bin/python" ]; then
    pyver=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    mkdir -p "$ROOT/venv/bin" "$ROOT/venv/lib/python$pyver/site-packages"
    printf 'home = /opt/bin\ninclude-system-site-packages = true\nversion = %s\n' "$pyver" > "$ROOT/venv/pyvenv.cfg"
    ln -sf /opt/bin/python3 "$ROOT/venv/bin/python"
fi
cp "$HERE/S90teplo" /opt/etc/init.d/S90teplo
chmod 755 /opt/etc/init.d/S90teplo
cp "$HERE/teplo-watchdog.sh" "$ROOT/teplo-watchdog.sh"
chmod 755 "$ROOT/teplo-watchdog.sh"
if [ ! -f "$ENV_FILE" ]; then
    cp "$HERE/teplo.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi
if ! grep -q '^DJANGO_SECRET_KEY=.' "$ENV_FILE"; then
    key=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
    sed -i '/^DJANGO_SECRET_KEY=/d' "$ENV_FILE"
    printf 'DJANGO_SECRET_KEY=%s\n' "$key" >> "$ENV_FILE"
fi
if ! grep -q teplo-watchdog /opt/etc/crontabs/root; then
    printf '\n# teplo watchdog: restart when the process dies or exceeds 300 MB RSS\n* * * * * %s/teplo-watchdog.sh >/dev/null 2>&1\n' "$ROOT" >> /opt/etc/crontabs/root
    # busybox crond rereads a changed crontab only when its owner is listed in cron.update
    echo root >> /opt/etc/crontabs/cron.update
fi
echo 'teplo prerequisites installed'
