#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$PWD" != "$HOME/PiBotPal" ]]; then
  echo 'The service template expects the checkout at ~/PiBotPal.' >&2
  exit 1
fi
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/pibotpal"
install -m 644 scripts/pibotpal.service "$HOME/.config/systemd/user/pibotpal.service"
systemctl --user daemon-reload
systemctl --user enable --now pibotpal.service
sudo loginctl enable-linger "$USER"
sudo tailscale serve --bg http://127.0.0.1:8765
