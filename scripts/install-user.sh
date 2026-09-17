#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA_DIR="$HOME/.local/share/artifact-hub"
CONFIG_DIR="$HOME/.config/artifact-hub"
LIB_DIR="$HOME/.local/lib/artifact-hub"
VENV_DIR="$LIB_DIR/venv"
UNIT_DIR="$HOME/.config/systemd/user"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$DATA_DIR" "$DATA_DIR/objects" "$DATA_DIR/incoming" "$DATA_DIR/trash"
mkdir -p "$CONFIG_DIR" "$LIB_DIR" "$UNIT_DIR" "$BIN_DIR"
chmod 700 "$DATA_DIR" "$DATA_DIR/objects" "$DATA_DIR/incoming" "$DATA_DIR/trash" "$CONFIG_DIR" "$LIB_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$PROJECT_DIR/requirements.lock"
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --no-deps "$PROJECT_DIR"

TOKEN_FILE="$CONFIG_DIR/publish.token"
if [[ ! -s "$TOKEN_FILE" ]]; then
  "$VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))' > "$TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

ENV_FILE="$CONFIG_DIR/service.env"
if [[ ! -e "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
ARTIFACT_HUB_HOST=127.0.0.1
ARTIFACT_HUB_PORT=8787
ARTIFACT_HUB_PUBLIC_BASE_URL=http://127.0.0.1:8787
ARTIFACT_HUB_DATA_DIR=$DATA_DIR
ARTIFACT_HUB_TOKEN_FILE=$TOKEN_FILE
ARTIFACT_HUB_DEFAULT_TTL=1209600
ARTIFACT_HUB_MAX_BYTES=536870912
ARTIFACT_HUB_QUOTA_BYTES=26843545600
ARTIFACT_HUB_MAX_UPLOADS=2
EOF
fi
chmod 600 "$ENV_FILE"

install -m 600 "$PROJECT_DIR/deploy/systemd/artifact-hub.service" "$UNIT_DIR/artifact-hub.service"
install -m 600 "$PROJECT_DIR/deploy/systemd/artifact-hub-gc.service" "$UNIT_DIR/artifact-hub-gc.service"
install -m 600 "$PROJECT_DIR/deploy/systemd/artifact-hub-gc.timer" "$UNIT_DIR/artifact-hub-gc.timer"
ln -sfn "$VENV_DIR/bin/artifact-hub" "$BIN_DIR/artifact-hub"
ln -sfn "$VENV_DIR/bin/artifact" "$BIN_DIR/artifact"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
"$VENV_DIR/bin/artifact-hub" init-db

systemctl --user daemon-reload
systemctl --user enable artifact-hub.service artifact-hub-gc.timer
systemctl --user restart artifact-hub.service
systemctl --user restart artifact-hub-gc.timer

if [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true) != yes ]]; then
  echo "WARNING: user lingering is disabled; the service may stop after logout." >&2
  echo "Ask an administrator to run: sudo loginctl enable-linger $USER" >&2
fi

echo "Artifact Hub installed."
echo "Endpoint: http://127.0.0.1:8787"
echo "Server CLI: $BIN_DIR/artifact-hub"
echo "Token: $TOKEN_FILE"
