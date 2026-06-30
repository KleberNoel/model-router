#!/usr/bin/env bash
# Idle watchdog for model router containers.
# Polls the llama-server /slots endpoint; kills container if idle for N minutes.
# Usage: ./idle-watchdog.sh <container-name> <container-ip> [idle-minutes]

set -euo pipefail

CONTAINER="${1:?Usage: $0 <container-name> <container-ip> [idle-minutes]}"
IP="${2:?Usage: $0 <container-name> <container-ip> [idle-minutes]}"
IDLE_MINUTES="${3:-10}"
SLEEP_INTERVAL=30
last_active=$(date +%s)

log() { echo "[$(date '+%H:%M:%S')] $*"; }

while true; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    log "Container $CONTAINER is gone. Exiting."
    exit 0
  fi

  slots=$(curl -sf -m 5 "http://$IP:8080/slots" 2>/dev/null || echo "")
  if [ -z "$slots" ]; then
    log "Warning: cannot reach $CONTAINER at $IP"
    sleep "$SLEEP_INTERVAL"
    continue
  fi

  # Check if any slot is currently processing
  processing=$(echo "$slots" | python3 -c "
import sys, json
try:
    slots = json.load(sys.stdin)
    active = [s for s in slots if s.get('is_processing', False)]
    print(len(active))
except: print(-1)
" 2>/dev/null || echo -1)

  now=$(date +%s)
  if [ "$processing" -gt 0 ]; then
    last_active=$now
    log "Active requests: $processing"
  else
    elapsed=$(( (now - last_active) / 60 ))
    if [ "$elapsed" -ge "$IDLE_MINUTES" ]; then
      log "Idle for ${elapsed}m (limit ${IDLE_MINUTES}m). Stopping $CONTAINER..."
      docker stop "$CONTAINER" --time 10
      log "Stopped $CONTAINER. Exiting."
      exit 0
    fi
  fi

  sleep "$SLEEP_INTERVAL"
done
