#!/usr/bin/env bash
# Rebuild frontend/openapi.json and the typescript-axios client it feeds.
#
#   scripts/regen-api.sh           rewrite both from the current backend code
#   scripts/regen-api.sh --check   fail if either is stale, change nothing
#
# The schema comes from the FastAPI app object, so this needs no backend on :8008
# and works in a git hook, in CI, and on a laptop with nothing running.
#
# The client half additionally needs a JDK and frontend/node_modules. When either
# is missing it is skipped with a warning rather than failing, because a checkout
# is expected to work without Java (see frontend/.gitignore). Set
# REGEN_API_STRICT=1 to make a missing toolchain an error instead — CI does.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CHECK=""
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) echo "usage: ${0##*/} [--check]" >&2; exit 2 ;;
esac

# Everything else the generator emits is gitignored, so drift can only surface here.
TRACKED=(api.ts base.ts common.ts configuration.ts index.ts .gitignore .npmignore)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo >&2
  echo "error: $1" >&2
  echo "       run 'make api', then stage frontend/openapi.json and frontend/src/api/generated" >&2
  exit 1
}

(cd "$ROOT/backend" && uv run python scripts/dump_openapi.py) > "$TMP/openapi.json"

if [ -n "$CHECK" ]; then
  diff -u "$ROOT/frontend/openapi.json" "$TMP/openapi.json" \
    || fail "frontend/openapi.json does not match the backend's schema"
else
  cp "$TMP/openapi.json" "$ROOT/frontend/openapi.json"
fi

missing=""
command -v java >/dev/null 2>&1 || missing="a JDK on PATH"
[ -x "$ROOT/frontend/node_modules/.bin/openapi-generator-cli" ] \
  || missing="${missing:+$missing and }frontend/node_modules (run 'npm ci' in frontend/)"

if [ -n "$missing" ]; then
  if [ -n "${REGEN_API_STRICT:-}" ]; then
    echo "error: cannot reach the generated client, missing $missing" >&2
    exit 1
  fi
  echo "warning: skipping the generated client, missing $missing" >&2
  exit 0
fi

# Generator version is pinned in frontend/openapitools.json; --no-install keeps a
# hook from silently reaching for the registry.
OUT="$ROOT/frontend/src/api/generated"
[ -n "$CHECK" ] && OUT="$TMP/generated"

(cd "$ROOT/frontend" && npx --no-install openapi-generator-cli generate \
  -i "$TMP/openapi.json" -g typescript-axios -o "$OUT" \
  --additional-properties=supportsES6=true >/dev/null)

if [ -n "$CHECK" ]; then
  for f in "${TRACKED[@]}"; do
    diff -u "$ROOT/frontend/src/api/generated/$f" "$OUT/$f" \
      || fail "frontend/src/api/generated/$f does not match the schema"
  done
  echo "openapi.json and the generated client are current"
else
  echo "regenerated frontend/openapi.json and frontend/src/api/generated"
fi
