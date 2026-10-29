#!/bin/sh
set -eu

ROOT=/opt/share/teplo
HERE=$(cd "$(dirname "$0")" && pwd)

opkg update
opkg install python3 python3-pip python3-sqlite3 python3-asyncio python3-cryptography
mkdir -p "$ROOT/releases" /opt/etc/teplo /opt/var/lib/teplo/static /opt/var/log/teplo /opt/var/run
[ -x "$ROOT/venv/bin/python" ] || python3 -m venv --system-site-packages --without-pip "$ROOT/venv"
cp "$HERE/S90teplo" /opt/etc/init.d/S90teplo
chmod 755 /opt/etc/init.d/S90teplo
cp "$HERE/teplo-watchdog.sh" "$ROOT/teplo-watchdog.sh"
chmod 755 "$ROOT/teplo-watchdog.sh"
if [ ! -f /opt/etc/teplo/teplo.env ]; then
    cp "$HERE/teplo.env.example" /opt/etc/teplo/teplo.env
    chmod 600 /opt/etc/teplo/teplo.env
fi
if ! grep -q teplo-watchdog /opt/etc/crontabs/root; then
    printf '\n# teplo watchdog: restart when the process dies or exceeds 300 MB RSS\n* * * * * %s/teplo-watchdog.sh >/dev/null 2>&1\n' "$ROOT" >> /opt/etc/crontabs/root
fi
echo 'teplo prerequisites installed'
