#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
HOST=${TEPLO_HOST:-192.168.1.1}
PORT=${TEPLO_SSH_PORT:-222}
ROOT=/opt/share/teplo
SHA=$(git rev-parse --short HEAD)
RELEASE=$ROOT/releases/$SHA

docker compose run --rm --no-deps -T app sh -c \
    'cd /code && poetry export --without dev --without-hashes --format requirements.txt' \
    | grep -v -E '^(cryptography|cffi|pycparser)==' > requirements.txt
ssh -p "$PORT" "root@$HOST" "mkdir -p $RELEASE"
git archive --format=tar HEAD app deploy | ssh -p "$PORT" "root@$HOST" "tar -x -C $RELEASE"
ssh -p "$PORT" "root@$HOST" "cat > $RELEASE/requirements.txt" < requirements.txt
ssh -p "$PORT" "root@$HOST" "set -eu
cd $RELEASE
$ROOT/venv/bin/python -m pip install --no-cache-dir --no-deps -r requirements.txt
set -a; . /opt/etc/teplo/teplo.env; set +a
$ROOT/venv/bin/python app/manage.py migrate --noinput
$ROOT/venv/bin/python app/manage.py collectstatic --noinput
$ROOT/venv/bin/python app/manage.py ensure_panel_user
ln -sfn $RELEASE $ROOT/current
/opt/etc/init.d/S90teplo restart"
echo "deployed $SHA"
