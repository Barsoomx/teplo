#!/bin/sh

PIDFILE=/opt/var/run/teplo.pid
LOGFILE=/opt/var/log/teplo/teplo.log
RESTARTS=/opt/var/log/teplo/restarts.log
SERVICE=/opt/etc/init.d/S90teplo
MAX_RSS_KB=307200
MAX_LOG_BYTES=5242880

restart() {
    echo "$(date '+%Y-%m-%dT%H:%M:%S') $1" >> "$RESTARTS"
    "$SERVICE" restart
}

if [ -f "$PIDFILE" ]; then
    if [ "$("$SERVICE" status)" != running ]; then
        restart 'not running'
    else
        rss=$(awk '/VmRSS/ {print $2}' "/proc/$(cat "$PIDFILE")/status")
        if [ "${rss:-0}" -gt "$MAX_RSS_KB" ]; then
            restart "rss ${rss} kB"
        fi
    fi
fi

if [ -f "$LOGFILE" ] && [ "$(wc -c < "$LOGFILE")" -gt "$MAX_LOG_BYTES" ]; then
    tail -c 1048576 "$LOGFILE" > "$LOGFILE.1"
    : > "$LOGFILE"
fi
