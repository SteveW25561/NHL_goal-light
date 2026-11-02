#!/usr/bin/env bash
# install.sh – one-shot setup for the NHL Goal Light on a fresh Raspberry Pi OS Lite card.
#
# Usage (as your normal user, NOT root):
#   git clone https://github.com/SteveW25561/NHL_goal-light.git ~/NHL_goal-light
#   cd ~/NHL_goal-light && ./install.sh && sudo reboot
#
# One run, then reboot. Safe to re-run (e.g. after git pull).
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Run as your normal user (the script uses sudo where needed)." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_LINK="$HOME/goal_light"          # nhl.py expects its files here
APP_USER="$(id -un)"
SERVICE=/etc/systemd/system/goal-light.service
BOOT_CFG=/boot/firmware/config.txt
TZ_NAME="${TZ_NAME:-America/Dawson_Creek}"   # BC permanent UTC-7 (see README)

echo "==> Installing packages"
sudo apt-get update
sudo apt-get install -y git python3-flask python3-requests python3-pygame alsa-utils
# GPIO: lgpio-backed RPi.GPIO shim on current Pi OS, classic RPi.GPIO as fallback
sudo apt-get install -y python3-rpi-lgpio || sudo apt-get install -y python3-rpi.gpio

echo "==> Linking $APP_LINK -> $REPO_DIR/goal_light"
if [[ -e "$APP_LINK" && ! -L "$APP_LINK" ]]; then
  echo "    $APP_LINK exists and is not a symlink; moving it to ${APP_LINK}.bak"
  mv "$APP_LINK" "${APP_LINK}.bak.$(date +%s)"
fi
ln -sfn "$REPO_DIR/goal_light" "$APP_LINK"

echo "==> Groups (audio, gpio)"
sudo usermod -aG audio,gpio "$APP_USER" || true

echo "==> Time zone: $TZ_NAME"
sudo timedatectl set-timezone "$TZ_NAME"

echo "==> WM8960 sound card in $BOOT_CFG"
add_cfg() { grep -qxF "$1" "$BOOT_CFG" || echo "$1" | sudo tee -a "$BOOT_CFG" >/dev/null; }
sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$BOOT_CFG"
add_cfg "dtparam=i2c_arm=on"
add_cfg "dtparam=i2s=on"
add_cfg "dtparam=audio=off"
add_cfg "dtoverlay=wm8960-soundcard"

echo "==> Default ALSA card -> wm8960soundcard"
sudo tee /etc/asound.conf >/dev/null <<'EOF'
pcm.!default {
  type plug
  slave.pcm "hw:wm8960soundcard"
}
ctl.!default {
  type hw
  card wm8960soundcard
}
EOF

echo "==> Fewer SD card writes (logs in RAM, noatime)"
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n' | sudo tee /etc/systemd/journald.conf.d/volatile.conf >/dev/null
sudo systemctl restart systemd-journald || true
if ! awk '$2=="/" && $4 ~ /noatime/ {f=1} END {exit !f}' /etc/fstab; then
  sudo sed -i -E '/^[^#]\S+\s+\/\s/ s/(\s)(defaults\S*)/\1\2,noatime/' /etc/fstab
fi

echo "==> Passwordless shutdown for the web UI button"
echo "$APP_USER ALL=(root) NOPASSWD: /usr/sbin/shutdown, /sbin/shutdown" | sudo tee /etc/sudoers.d/goal-light >/dev/null
sudo chmod 440 /etc/sudoers.d/goal-light
sudo visudo -cf /etc/sudoers.d/goal-light

echo "==> systemd service"
sudo tee "$SERVICE" >/dev/null <<EOF
[Unit]
Description=NHL Goal Light
Wants=network-online.target sound.target
After=network-online.target sound.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_LINK
ExecStartPre=-$REPO_DIR/audio-init.sh
ExecStart=/usr/bin/python3 $APP_LINK/nhl.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable goal-light.service

if systemctl is-active --quiet goal-light.service; then
  sudo systemctl restart goal-light.service
fi
echo "==> Done. Reboot now: sudo reboot"
echo "    Then open http://$(hostname -I | awk '{print $1}'):8080"
