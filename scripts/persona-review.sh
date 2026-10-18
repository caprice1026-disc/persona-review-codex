#!/bin/sh
# Python is required; this launcher never installs it or changes the caller's cwd.
set -eu
case $0 in
    */*) script_dir=${0%/*} ;;
    *) script_dir=. ;;
esac
script_dir=$(CDPATH= cd -- "$script_dir" && pwd)
PYTHON_MANAGER_AUTOMATIC_INSTALL=false
export PYTHON_MANAGER_AUTOMATIC_INSTALL
unset PYLAUNCHER_ALLOW_INSTALL PYLAUNCHER_ALWAYS_INSTALL
probe='import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'

usable() {
    command -v "$1" >/dev/null 2>&1 && "$1" -I -c "$probe" >/dev/null 2>&1
}

if [ -n "${PERSONA_REVIEW_PYTHON-}" ]; then
    if usable "$PERSONA_REVIEW_PYTHON"; then
        exec "$PERSONA_REVIEW_PYTHON" -I "$script_dir/persona_review.py" "$@"
    fi
else
    for candidate in python3 python; do
        if usable "$candidate"; then
            exec "$candidate" -I "$script_dir/persona_review.py" "$@"
        fi
    done
fi
printf '%s\n' 'persona-review: Python 3.10+ was not found or could not run.' >&2
printf '%s\n' 'Install Python from https://www.python.org/downloads/ or your OS package manager.' >&2
printf '%s\n' 'Or set PERSONA_REVIEW_PYTHON to one trusted executable path (no extra arguments).' >&2
printf '%s\n' 'No Python installation or configuration write was performed.' >&2
exit 127
