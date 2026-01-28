#!/bin/bash
# Build and deploy Paperboy Docker image
#
# This script handles copying arxiv-src-ir into the build context,
# building the image, and optionally restarting the container.
#
# Usage:
#   ./docker/build.sh          # Build only
#   ./docker/build.sh deploy   # Build and restart container

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
ARXIV_SRC_IR_PATH="${ARXIV_SRC_IR_PATH:-/home/demitri/repositories/arxiv-src-ir/python}"

cd "$REPO_ROOT"

echo "==> Checking arxiv-src-ir source..."
if [ ! -d "$ARXIV_SRC_IR_PATH" ]; then
    echo "ERROR: arxiv-src-ir not found at: $ARXIV_SRC_IR_PATH"
    echo ""
    echo "Set ARXIV_SRC_IR_PATH environment variable to the correct location:"
    echo "  export ARXIV_SRC_IR_PATH=/path/to/arxiv-src-ir/python"
    exit 1
fi

echo "==> Copying arxiv-src-ir into build context..."
rm -rf "$REPO_ROOT/arxiv-src-ir"
cp -r "$ARXIV_SRC_IR_PATH" "$REPO_ROOT/arxiv-src-ir"

cleanup() {
    echo "==> Cleaning up arxiv-src-ir copy..."
    rm -rf "$REPO_ROOT/arxiv-src-ir"
}
trap cleanup EXIT

echo "==> Building Docker image..."
docker build -f docker/Dockerfile -t paperboy:latest .

if [ "$1" = "deploy" ]; then
    echo "==> Restarting container..."
    docker compose -f docker/docker-compose.yml up -d

    echo "==> Waiting for health check..."
    sleep 5

    if docker ps | grep -q "paperboy.*healthy"; then
        echo "==> Deployment successful!"
    else
        echo "==> Container started. Checking health..."
        docker compose -f docker/docker-compose.yml logs --tail=20
    fi
else
    echo ""
    echo "Build complete. To deploy, run:"
    echo "  ./docker/build.sh deploy"
    echo ""
    echo "Or manually:"
    echo "  docker compose -f docker/docker-compose.yml up -d"
fi
