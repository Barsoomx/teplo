#!/bin/sh
# Regenerates docs/assets from a demo house: two panel instances (English and Russian) and a Playwright browser.
# DOCKER and MOUNT_SOURCE override the docker binary and the bind-mount path (e.g. docker.exe and C:\... under WSL).
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
docker=${DOCKER:-docker}
source=${MOUNT_SOURCE:-$root}
network=teplo-shots

# The demo house clock always reads 07:xx, whenever the screenshots are taken, so the demo schedule uses fixed times.
hour=$(date -u +%H | sed 's/^0//')
ahead=$(((31 - hour) % 24))
if [ "$ahead" -le 14 ]; then zone="Etc/GMT-$ahead"; else zone="Etc/GMT+$((24 - ahead))"; fi

stop() {
    "$docker" rm -f teplo-demo-en teplo-demo-ru >/dev/null 2>&1 || true
    "$docker" network rm "$network" >/dev/null 2>&1 || true
}
trap stop EXIT

"$docker" build -q -t teplo-dev "$source" >/dev/null
"$docker" build -q -t teplo-shots "$source/scripts/demo" >/dev/null
stop
"$docker" network create "$network" >/dev/null

for language in en ru; do
    "$docker" run -d --name "teplo-demo-$language" --network "$network" \
        --mount "type=bind,source=$source,target=/code" \
        -e DJANGO_SETTINGS_MODULE=settings.settings \
        -e DJANGO_SECRET_KEY=teplo-demo-only \
        -e DJANGO_DEBUG=1 \
        -e TEPLO_SITE=barsoom \
        -e TEPLO_DB_PATH=/tmp/demo.sqlite3 \
        -e TEPLO_DRY_RUN=false \
        -e TEPLO_CLOCK_SOURCE=system \
        -e TEPLO_TIME_ZONE="$zone" \
        -e TEPLO_ALLOWED_HOSTS="teplo-demo-$language" \
        -e DEMO_LANGUAGE="$language" \
        -e PYTHONPATH=/code/scripts/demo \
        teplo-dev python manage.py shell -c 'import demo_house; demo_house.main()' >/dev/null
done

"$docker" run --rm --network "$network" --mount "type=bind,source=$source,target=/code" \
    teplo-shots python /code/scripts/demo/capture.py
ls -l "$root/docs/assets"
