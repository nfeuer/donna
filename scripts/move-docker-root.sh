#!/usr/bin/env bash
# Move Docker's data-root from the OS disk to a dedicated volume.
# Usage: sudo ./move-docker-root.sh [DESTINATION]
#   DESTINATION defaults to $DONNA_DATA_PATH/docker, or /donna/docker if unset.
set -euo pipefail

SRC="/var/lib/docker"
DST="${1:-${DONNA_DATA_PATH:-/donna}/docker}"

echo "==> Stopping Docker..."
systemctl stop docker docker.socket containerd

echo "==> Copying $SRC -> $DST (this may take a few minutes)..."
rsync -aHAXS "$SRC/" "$DST/"

echo "==> Updating /etc/docker/daemon.json..."
# Preserve existing runtimes block if nvidia-container-runtime is installed.
cat > /etc/docker/daemon.json <<EOF
{
    "data-root": "$DST",
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    }
}
EOF

echo "==> Starting Docker..."
systemctl start docker

echo "==> Verifying..."
docker info --format '{{.DockerRootDir}}'
docker ps --format 'table {{.Names}}\t{{.Status}}' | head -20

echo ""
echo "Done! Docker data-root is now at $DST."
echo "Once you verify everything works, remove the old data with:"
echo "  sudo rm -rf /var/lib/docker"
