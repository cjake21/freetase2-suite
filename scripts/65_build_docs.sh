#!/usr/bin/env bash
set -Eeuo pipefail

# Build the HTML documentation that the console serves at /docs/.
#
# Uses an isolated virtualenv (docs/.venv, gitignored) built from
# docs/requirements.txt, so the build does not depend on whatever Sphinx
# happens to be installed system-wide. This is what avoids the common
# "could not import extension myst_parser" error: a partial system install
# (sphinx alone, without myst-parser or the RTD theme) never gets used.
#
#   ./scripts/65_build_docs.sh        # create venv if needed, build HTML
#
# Needs python3-venv (scripts/00_install_deps.sh installs it).

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS="$PROJECT/docs"
VENV="$DOCS/.venv"

if [[ ! -x "$VENV/bin/sphinx-build" ]]; then
  echo "[docs] creating build venv at $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$DOCS/requirements.txt"
fi

echo "[docs] building HTML"
"$VENV/bin/sphinx-build" -q -b html "$DOCS" "$DOCS/_build/html"
echo "[docs] built -> docs/_build/html (the console serves this at /docs/)"
