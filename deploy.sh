#!/bin/bash
# deploy.sh — Єдиний скрипт деплою Worker + Dashboard
#
# Usage:
#   ./deploy.sh           — деплой Worker + Dashboard
#   ./deploy.sh worker    — тільки Worker
#   ./deploy.sh dashboard — тільки Dashboard
#   ./deploy.sh db        — міграція бази даних
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.env"

export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID

deploy_worker() {
    echo "=== Deploying Worker ==="
    cd "$SCRIPT_DIR/worker"
    npx wrangler deploy 2>&1 | tail -3
    echo "Worker deployed ✓"
}

deploy_dashboard() {
    echo "=== Deploying Dashboard ==="
    cd "$SCRIPT_DIR"
    npx wrangler pages deploy dashboard \
        --project-name radacleaner-dashboard \
        --branch main \
        --commit-dirty=true 2>&1 | tail -3
    echo "Dashboard deployed ✓"
}

deploy_db() {
    local migration="$1"
    if [ -z "$migration" ]; then
        echo "Usage: ./deploy.sh db <migration_file>"
        exit 1
    fi
    echo "=== Running migration: $migration ==="
    cd "$SCRIPT_DIR"
    npx wrangler d1 execute radacleaner-db --remote --file="$migration"
    echo "Migration done ✓"
}

case "${1:-all}" in
    worker)     deploy_worker ;;
    dashboard)  deploy_dashboard ;;
    db)         deploy_db "$2" ;;
    all)
        deploy_worker
        deploy_dashboard
        ;;
    *)
        echo "Usage: ./deploy.sh [worker|dashboard|db <migration>|all]"
        exit 1
        ;;
esac
