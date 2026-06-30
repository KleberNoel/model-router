#!/bin/bash
# llama-server wrapper for model-router managed mode.
# Translates llama-server CLI calls into Docker container lifecycle.

set -uo pipefail

MODEL_PATH=""
ALIAS=""
PORT="8090"
EXTRA_ARGS=()
CONTAINER_IMAGE="${LLAMA_SERVER_IMAGE:-vito974/llama-cpp-turboquant:server-cuda12}"
MODELS_HOST_DIR="${MODELS_HOST_DIR:-/home/kleber/models}"
DOCKER_NETWORK="${DOCKER_NETWORK:-model-router_ai-stack}"

while [ $# -gt 0 ]; do
  case "$1" in
    -m|--model) MODEL_PATH="$2"; shift 2 ;;
    -m=*|--model=*) MODEL_PATH="${1#*=}"; shift ;;
    --alias) ALIAS="$2"; shift 2 ;;
    --alias=*) ALIAS="${1#*=}"; shift ;;
    --host) shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    *) EXTRA_ARGS+=("$1"); shift ;;
  esac
done

if [ -z "$MODEL_PATH" ]; then
  echo "FATAL: no model path provided" >&2
  exit 1
fi

INTERNAL_MODEL="$MODEL_PATH"
MODEL_NAME=$(basename "$MODEL_PATH")
HOST_MODEL="$MODELS_HOST_DIR/$MODEL_NAME"

if [ ! -f "$INTERNAL_MODEL" ]; then
  echo "FATAL: model not found at $INTERNAL_MODEL" >&2
  exit 1
fi

CONTAINER_NAME="managed-${ALIAS}-$(date +%s)"

start_container() {
  docker run -d --rm \
    --name "$CONTAINER_NAME" \
    --network "$DOCKER_NETWORK" \
    --gpus all \
    --ulimit memlock=64000000:64000000 \
    --entrypoint /bin/bash \
    -v "$HOST_MODEL:$INTERNAL_MODEL:ro" \
    "$CONTAINER_IMAGE" \
    -lc "exec /app/llama-server -m $INTERNAL_MODEL --host 0.0.0.0 --port 8080 --alias ${ALIAS} -ngl all --no-mmap --mlock ${EXTRA_ARGS[*]}"
}

get_container_ip() {
  docker inspect "$CONTAINER_NAME" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true
}

echo "[wrapper] Starting container $CONTAINER_NAME for $ALIAS on port $PORT" >&2
start_container >/dev/null 2>&1

echo "[wrapper] Waiting for $ALIAS to become ready..." >&2

for i in $(seq 1 180); do
  CIP=$(get_container_ip)
  if [ -n "$CIP" ]; then
    if python3 -c "import urllib.request, sys; urllib.request.urlopen('http://${CIP}:8080/v1/models', timeout=3); sys.exit(0)" 2>/dev/null; then
      echo "[wrapper] Ready at ${CIP}:8080" >&2
      CONTAINER_IP="$CIP"
      break
    fi
  fi
  sleep 1
done

if [ -z "${CONTAINER_IP:-}" ]; then
  echo "[wrapper] FAILED: container never became ready" >&2
  docker logs "$CONTAINER_NAME" 2>/dev/null | tail -5
  docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
  exit 1
fi

echo "[wrapper] $ALIAS is live. Proxy active on internal port $PORT." >&2

python3 -c "
import socket, threading, sys

def forward(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except: pass
    finally:
        for s in (src, dst):
            try: s.close()
            except: pass

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('0.0.0.0', ${PORT}))
server.listen(10)
sys.stderr.write('[proxy] Listening on 0.0.0.0:${PORT}, forwarding to ${CONTAINER_IP}:8080\n')
sys.stderr.flush()
while True:
    client, addr = server.accept()
    backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    backend.connect(('${CONTAINER_IP}', 8080))
    threading.Thread(target=forward, args=(client, backend), daemon=True).start()
    threading.Thread(target=forward, args=(backend, client), daemon=True).start()
" &
PROXY_PID=$!

cleanup() {
  docker stop "$CONTAINER_NAME" --timeout 10 2>/dev/null || true
  kill "$PROXY_PID" 2>/dev/null || true
  wait "$PROXY_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait
