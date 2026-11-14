#!/usr/bin/env bash
set -euo pipefail
git diff --quiet && git diff --cached --quiet || { echo "Uncommitted changes - commit first"; exit 1; }
for i in 1 2 3 4 5; do
  git push origin main && git push origin main:live && break
  echo "Push failed (network?), retry $i/5 in 10s"; sleep 10
done
WANT=$(git rev-parse --short=8 HEAD); echo "Waiting for the server to go live with $WANT ..."
for i in $(seq 1 36); do
  GOT=$(curl -fsS --max-time 10 https://stoprop.tech/api/health 2>/dev/null \
        | grep -o '"commit": *"[^"]*"' | grep -o '[0-9a-f]\{8\}' || true)
  [ "$GOT" = "$WANT" ] && { echo "LIVE: $WANT"; exit 0; }
  sleep 10
done
echo "Not live after 6 min. Check: ssh stoprop 'tail -40 /var/log/stoprop-deploy.log'"; exit 1
