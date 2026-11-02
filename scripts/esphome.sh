#!/bin/sh
# Builds the ESPHome nodes in esphome/ with the pinned ESPHome image:
#   sh scripts/esphome.sh config [node...]           validate the configurations
#   sh scripts/esphome.sh compile [node...]          build firmware and print where each factory image landed
#   sh scripts/esphome.sh upload <node> <address>    build one node and flash it over the air
# Without node names, config and compile take every esphome/teplo-*.yaml.
# DOCKER and MOUNT_SOURCE override the docker binary and the bind-mount path (e.g. docker.exe and C:\... under WSL).
# With docker.exe, upload forwards the node's OTA port from WSL: Docker Desktop's containers do not reach the LAN.
# ESPHOME_JOBS caps the parallel compile jobs, 3 by default.
set -eu

image=ghcr.io/esphome/esphome:2026.10.1
root=$(cd "$(dirname "$0")/.." && pwd)
docker=${DOCKER:-docker}
source=${MOUNT_SOURCE:-$root}
jobs=${ESPHOME_JOBS:-3}

usage() {
    echo 'usage: sh scripts/esphome.sh config [node...] | compile [node...] | upload <node> <address>' >&2
    exit 2
}

check_nodes() {
    for node in "$@"; do
        if [ ! -f "$root/esphome/$node.yaml" ]; then
            echo "esphome/$node.yaml does not exist" >&2
            exit 2
        fi
    done
}

# Toolchains and builds stay in Docker volumes: a bind mount from Windows makes a build crawl.
run() {
    "$docker" run --rm \
        --mount "type=bind,source=$source,target=/code" \
        --mount type=volume,source=teplo-esphome-cache,target=/cache \
        --mount type=volume,source=teplo-esphome-build,target=/build \
        -e ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT="$jobs" \
        "$@"
}

compile_node() {
    run --entrypoint sh "$image" -ec '
        /entrypoint.sh compile "/code/esphome/$1.yaml"
        mkdir -p /code/esphome/.esphome/firmware
        cp "/build/$1/build/firmware.factory.bin" "/code/esphome/.esphome/firmware/$1.factory.bin"
    ' sh "$1"
}

host_path() {
    case $source in
        *\\*) printf '%s\\%s\n' "$source" "$(printf '%s' "$1" | tr '/' '\\')" ;;
        *) printf '%s/%s\n' "$source" "$1" ;;
    esac
}

command=${1:-}
[ "$#" -eq 0 ] || shift

case $command in
    config | compile)
        if [ "$#" -eq 0 ]; then
            for file in "$root"/esphome/teplo-*.yaml; do
                set -- "$@" "$(basename "$file" .yaml)"
            done
        fi
        check_nodes "$@"
        ;;
    upload)
        [ "$#" -eq 2 ] || usage
        check_nodes "$1"
        ;;
    *)
        usage
        ;;
esac

case $command in
    config)
        for node in "$@"; do
            run "$image" config "/code/esphome/$node.yaml"
        done
        ;;
    compile)
        for node in "$@"; do
            compile_node "$node"
        done
        echo
        for node in "$@"; do
            printf '%s: %s\n' "$node" "$(host_path "esphome/.esphome/firmware/$node.factory.bin")"
        done
        ;;
    upload)
        device=$2
        case $docker in
            *.exe)
                python3 "$root/scripts/ota_forward.py" "$2" 3232 &
                forward=$!
                trap 'kill "$forward" 2>/dev/null' EXIT
                sleep 1
                device=host.docker.internal
                ;;
        esac
        run "$image" run "/code/esphome/$1.yaml" --device "$device" --ota-platform esphome --no-logs
        ;;
esac
