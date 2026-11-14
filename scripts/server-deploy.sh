#!/usr/bin/env bash
set -uo pipefail
exec 9>/tmp/stoprop-deploy.lock; flock -n 9 || exit 0          # one deploy at a time
cd /docker/stoprop
git fetch -q origin live || exit 0                              # network hiccup: try next minute
NEW=$(git rev-parse origin/live); CUR=$(git rev-parse HEAD)
if [ "$NEW" = "$CUR" ] && [ "${1:-}" != "--force" ]; then exit 0; fi
echo "$(date -Is) deploying ${NEW:0:8} (was ${CUR:0:8})"
build() { export GIT_COMMIT=$(git rev-parse --short=8 HEAD); docker compose up -d --build; }
healthy() { for i in $(seq 1 12); do
    docker compose exec -T stoprop python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)" >/dev/null 2>&1 && return 0
    sleep 5; done; return 1; }
git reset -q --hard "$NEW"        # tracked files only; .env and data/ are untracked + gitignored
if build && healthy; then echo "$(date -Is) OK ${NEW:0:8}"
else echo "$(date -Is) FAILED ${NEW:0:8} -> rolling back to ${CUR:0:8}"
     git reset -q --hard "$CUR"; build; fi
