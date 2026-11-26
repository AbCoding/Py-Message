#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"
python3 -c 'import sys; sys.exit("Py-Message requires Python 3.10 or newer.") if sys.version_info < (3, 10) else None'
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
mkdir -p .tools
if ! cmp -s requirements-lock.txt .tools/requirements-installed.txt; then
    if [ -f /etc/ssl/cert.pem ]; then
        .venv/bin/python -m pip install --disable-pip-version-check --cert /etc/ssl/cert.pem -r requirements-lock.txt
    else
        .venv/bin/python -m pip install --disable-pip-version-check -r requirements-lock.txt
    fi
    cp requirements-lock.txt .tools/requirements-installed.txt
fi
if [ "$#" -eq 0 ]; then
    set -- app
fi
exec .venv/bin/python main.py "$@"
