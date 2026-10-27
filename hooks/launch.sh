#!/bin/sh
# Run one Foreman hook under uv. A hook may start with a PATH that lacks uv, so look in the usual places.
script="$1"
dir="$(cd "$(dirname "$0")" && pwd)"
for uv in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
  if [ -n "$uv" ] && [ -x "$uv" ]; then
    if [ "$script" = "guard.py" ]; then
      # Any failure, even one Python reports before the guard runs (exit 1, which Claude Code lets through), blocks.
      "$uv" run --no-project --python 3.12 "$dir/$script" || exit 2
      exit 0
    fi
    exec "$uv" run --no-project --python 3.12 "$dir/$script"
  fi
done
echo "foreman: uv not found, so $script cannot run. Install uv: https://docs.astral.sh/uv/" >&2
# The guard fails closed: a push it cannot check must not go through.
[ "$script" = "guard.py" ] && exit 2
exit 0
