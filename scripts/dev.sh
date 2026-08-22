#!/usr/bin/env sh
# Wrapper around docker compose for the dev stack.
#
# Exists because compose interpolates ${DEV_BIND_IP} from the shell, not from
# env_file:. Rather than storing the host's tailnet address in a file, derive
# it from the interface -- the repo is public and should contain no internal
# addresses, and a missing interface should be a loud failure, not a silent
# fallback to something publicly reachable.
#
# Usage: ./scripts/dev.sh up -d app
#        ./scripts/dev.sh --profile tools run --rm tools npm test
set -eu

DEV_BIND_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"

if [ -z "${DEV_BIND_IP}" ]; then
  echo "error: no IPv4 address on tailscale0 - is Tailscale up?" >&2
  echo "       refusing to start: without it the port would bind every interface." >&2
  exit 1
fi

UID_="$(id -u)"
GID_="$(id -g)"

export DEV_BIND_IP
export UID="${UID_}"
export GID="${GID_}"

exec docker compose -f "$(dirname "$0")/../docker-compose.dev.yml" "$@"
