#!/bin/sh
# Single offline entry point. Docker endpoint/discovery is handled by the manager.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -B "$base/installer/manage.py" "$@"
