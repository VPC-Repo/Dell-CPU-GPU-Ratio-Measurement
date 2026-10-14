#!/bin/bash
# Install the measurement kit on this server and start its page.
#
#   ./install.sh            install Docker if needed (Ubuntu), build the images, start
#   ./install.sh --pull     pull prebuilt images (BENCH_IMAGE and EMBED_IMAGE in .env)
#                           instead of building them
#
# Then run ./measure. The page is on 127.0.0.1:8766: from your laptop,
#   ssh -L 8766:127.0.0.1:8766 <this server>   and open http://127.0.0.1:8766
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n== %s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  . /etc/os-release 2>/dev/null || true
  if [ "${ID:-}" != "ubuntu" ]; then
    echo "Docker is not installed. Install Docker Engine with the Compose plugin (2.24 or newer), then run ./install.sh again."
    exit 1
  fi
  say "Installing Docker Engine (needs sudo once)"
  sudo apt-get update -q
  sudo apt-get install -y -q ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -q
  sudo apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo usermod -aG docker "$USER" || true
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
    echo "Using sudo for Docker in this session (log out and back in to use it without sudo)."
  else
    echo "Docker is installed but not reachable. Start it (sudo systemctl start docker) and run ./install.sh again."
    exit 1
  fi
fi
COMPOSE_VERSION=$($DOCKER compose version --short 2>/dev/null || true)
if [ -z "$COMPOSE_VERSION" ]; then
  echo "The Docker Compose plugin is missing. Install docker-compose-plugin and run ./install.sh again."
  exit 1
fi
if [ "$(printf '%s\n2.24.0\n' "${COMPOSE_VERSION#v}" | sort -V | head -1)" != "2.24.0" ]; then
  echo "Docker Compose $COMPOSE_VERSION is too old. The kit needs 2.24 or newer."
  exit 1
fi

mkdir -p data
if [ "${1:-}" = "--pull" ]; then
  say "Pulling the images"
  $DOCKER compose pull bench
  $DOCKER compose --profile images pull embedder
else
  say "Building the images (needs internet, 15 to 35 minutes)"
  $DOCKER compose build bench
  $DOCKER compose --profile images build embedder
fi
$DOCKER pull -q "$(grep -E '^POSTGRES_IMAGE=' .env 2>/dev/null | cut -d= -f2 || true)" 2>/dev/null || $DOCKER pull -q postgres:16-alpine

say "Starting the page"
$DOCKER compose up -d bench
sleep 5
$DOCKER compose exec -T -u bench bench /opt/bench/.venv/bin/python -m runner.preflight --quick || true

UI_PORT=$(grep -E '^UI_PORT=' .env 2>/dev/null | cut -d= -f2 || true); UI_PORT=${UI_PORT:-8766}
cat <<EOF

Installed. Next:
  ./measure            runs every measurement (about 1 h 50 min)
$( [ "$DOCKER" = "sudo docker" ] && echo "  (./measure uses sudo for Docker until you log out and back in)" )
From your laptop:
  ssh -L $UI_PORT:127.0.0.1:$UI_PORT $(hostname)    then open http://127.0.0.1:$UI_PORT
EOF
