#!/usr/bin/env bash
# Update a Plesk installation: pull, install, migrate, collect static files, restart.
# Run from the subscription's SSH shell:  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

git pull --ff-only
.venv/bin/pip install --quiet -r requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput --verbosity 0
.venv/bin/python manage.py check --deploy || true   # warnings only

mkdir -p tmp
touch tmp/restart.txt   # Passenger reloads the app on the next request
echo "ERGAVON updated and restarted."
