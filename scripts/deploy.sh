#!/bin/bash
# deploy.sh — Deploy dashboard to dev or main server
# Usage: ./deploy.sh [dev|main]
#
# dev  = port 8081 (dev branch)
# main = port 8080 (main branch)

set -e

BRANCH="${1:-dev}"
REPO_DIR="/home/radamon/radacleaner"
DASHBOARD_SRC="${REPO_DIR}/dashboard/index.html"

if [ "$BRANCH" = "dev" ]; then
    PORT=8081
    DEPLOY_DIR="/home/radamon/straj-dev"
    SCREEN_NAME="dev-server"
    LABEL="DEV"
elif [ "$BRANCH" = "main" ]; then
    PORT=8080
    DEPLOY_DIR="/home/radamon/straj"
    SCREEN_NAME="main-server"
    LABEL="MAIN"
else
    echo "Usage: $0 [dev|main]"
    exit 1
fi

echo "=== Deploying to ${LABEL} (port ${PORT}, branch ${BRANCH}) ==="

# 1. Pull latest code
cd "$REPO_DIR"
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" origin/"$BRANCH"
git pull --rebase origin "$BRANCH"

# 2. Copy dashboard files
cp "$DASHBOARD_SRC" "$DEPLOY_DIR/index.html"

# 3. Restart web server
pkill -f "http.server $PORT" 2>/dev/null || true
sleep 1
screen -dmS "$SCREEN_NAME" bash -c "cd $DEPLOY_DIR && python3 -m http.server $PORT --bind 0.0.0.0"

# 4. Verify
sleep 1
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/")
if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ ${LABEL} deployed successfully! http://192.168.1.235:${PORT}/"
else
    echo "❌ ${LABBEL} deployment failed (HTTP ${HTTP_CODE})"
    exit 1
fi