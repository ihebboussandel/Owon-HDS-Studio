#!/usr/bin/env bash
set -euo pipefail
studio_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${HDS_STUDIO_PYTHON:-}" ]]; then
    exec "$HDS_STUDIO_PYTHON" "$studio_root/launch.py" "$@"
elif [[ -x /media/iheb/data/Ai_codes/miniconda3/envs/tf218/bin/python ]]; then
    exec /media/iheb/data/Ai_codes/miniconda3/envs/tf218/bin/python "$studio_root/launch.py" "$@"
else
    exec python3 "$studio_root/launch.py" "$@"
fi
