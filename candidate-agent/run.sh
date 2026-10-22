#!/usr/bin/env bash
# Start the Candidate Agent with keys from .env loaded (optional; plain `claude` works too).
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec claude "$@"
