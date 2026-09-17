#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 1 || $1 != https://* ]]; then
  echo "Usage: $0 https://your-host.your-tailnet.ts.net" >&2
  exit 2
fi

ENV_FILE="$HOME/.config/artifact-hub/service.env"
PUBLIC_URL=${1%/}
python3 - "$ENV_FILE" "$PUBLIC_URL" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
public_url = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
key = "ARTIFACT_HUB_PUBLIC_BASE_URL="
updated = [key + public_url if line.startswith(key) else line for line in lines]
if not any(line.startswith(key) for line in lines):
    updated.append(key + public_url)
temporary = path.with_suffix(".tmp")
temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY

systemctl --user restart artifact-hub.service
echo "Artifact Hub public URL set to $PUBLIC_URL"
