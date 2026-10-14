#!/bin/bash
# Container start: give the run user the host's Docker socket group, then
# serve the page (the measurement itself starts with ./measure on the host).
set -euo pipefail
if [ -S /var/run/docker.sock ]; then
  gid=$(stat -c %g /var/run/docker.sock)
  grp=$(getent group "$gid" | cut -d: -f1 || true)
  [ -n "$grp" ] || { groupadd -g "$gid" hostdocker; grp=hostdocker; }
  usermod -aG "$grp" bench
fi
mkdir -p /opt/bench/data/ui /opt/bench/data/capacity
chown bench:bench /opt/bench/data /opt/bench/data/ui /opt/bench/data/capacity
case "${1:-serve}" in
  serve) exec sudo -E -H -u bench /opt/bench/.venv/bin/python -m runner.serve ;;
  *)     exec "$@" ;;
esac
