#!/bin/bash
# deploy.sh — Deploy dashboard to dev server or Cloudflare Pages
# Usage: ./deploy.sh [dev|pages]
#
# dev   = port 8081 (локальний HTTP сервер)
# pages = Cloudflare Pages + Worker API

set -e

BRANCH="${1:-dev}"
REPO_DIR="/home/radamon/radacleaner"
DASHBOARD_SRC="${REPO_DIR}/dashboard/index.html"

if [ "$BRANCH" = "dev" ]; then
    PORT=8081
    DEPLOY_DIR="/home/radamon/straj-dev"
    SCREEN_NAME="dev-server"
    LABEL="DEV"

    echo "=== Deploying to ${LABEL} (port ${PORT}) ==="

    cd "$REPO_DIR"
    cp "$DASHBOARD_SRC" "$DEPLOY_DIR/index.html"

    pkill -f "http.server $PORT" 2>/dev/null || true
    sleep 1
    screen -dmS "$SCREEN_NAME" bash -c "cd $DEPLOY_DIR && python3 -m http.server $PORT --bind 0.0.0.0"

    sleep 1
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "✅ ${LABEL} deployed! http://192.168.1.235:${PORT}/"
    else
        echo "❌ ${LABEL} deployment failed (HTTP ${HTTP_CODE})"
        exit 1
    fi

elif [ "$BRANCH" = "pages" ]; then
    echo "=== Deploying to Cloudflare Pages ==="

    cd "$REPO_DIR"
    npx wrangler pages deploy dashboard --project-name radacleaner-dashboard --branch main

    echo "✅ Dashboard on Pages: https://radacleaner-dashboard.pages.dev"
    echo "✅ Worker API: https://rada-monitor-api.distih.workers.dev"
fi