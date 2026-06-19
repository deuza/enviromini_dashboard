#!/bin/sh
DIR=$(dirname -- "$(readlink -f -- "$0")")
exec "$DIR/.env/bin/python3" "$DIR/enviromini_dashboard.py" "$@"
