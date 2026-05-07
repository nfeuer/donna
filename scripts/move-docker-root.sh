#!/usr/bin/env bash
set -euo pipefail

SRC="/var/lib/docker"
DST="/mnt/donna/docker"

echo "==> Stopping Docker..."
systemctl stop docker docker.socket containerd

echo "==> Copying $SRC -> $DST (this may take a few minutes)..."
rsync -aHAXS "$SRC/" "$DST/"

echo "==> Updating /etc/docker/daemon.json..."
cat > /etc/docker/daemon.json <<'EOF'
{
    "data-root": "/mnt/donna/docker",
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
echo "Done! Docker data-root is now on /mnt/donna/docker."
echo "Once you verify everything works, remove the old data with:"
echo "  sudo rm -rf /var/lib/docker"
