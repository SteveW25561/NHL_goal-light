#!/usr/bin/env bash
# readonly.sh – run the Pi with a read-only root filesystem (overlay), so pulling
# the plug can't corrupt the SD card. Settings (config.json) move to the boot
# partition, which stays writable and is only written when you click Save Config.
#
#   ./readonly.sh enable    # move settings, turn overlay on, reboot
#   ./readonly.sh disable   # turn overlay off (needed before git pull / apt upgrade), reboot
#   ./readonly.sh status
#
# While enabled, any change outside the boot partition (git pull, apt, edits in ~)
# is thrown away at the next reboot.
set -euo pipefail

APP_USER="$(id -un)"
BOOT=/boot/firmware
CFG_DIR="$BOOT/goal-light"
CFG="$CFG_DIR/config.json"
OLD_CFG="$HOME/goal_light/config.json"
DROPIN=/etc/systemd/system/goal-light.service.d/config-path.conf

overlay_on() { grep -q "overlayroot=tmpfs" "$BOOT/cmdline.txt" 2>/dev/null; }

status() {
  if overlay_on; then echo "overlay: ENABLED in cmdline.txt (read-only root after reboot)"; else echo "overlay: disabled"; fi
  if mount | grep -q " on / type overlay"; then echo "root right now: read-only overlay"; else echo "root right now: normal (writable)"; fi
  if [[ -f "$DROPIN" ]]; then echo "settings file: $CFG"; else echo "settings file: $OLD_CFG"; fi
}

enable() {
  [[ $EUID -ne 0 ]] || { echo "Run as your normal user." >&2; exit 1; }
  if mount | grep -q " on / type overlay"; then
    echo "Root is already a read-only overlay. Run './readonly.sh disable', reboot, then enable again." >&2
    exit 1
  fi

  echo "==> Making the boot partition writable by $APP_USER"
  # FAT has no per-file permissions, so ownership comes from the mount options.
  if ! awk -v b="$BOOT" '$2==b && $4 ~ /uid=/ {f=1} END {exit !f}' /etc/fstab; then
    sudo sed -i -E "\|^[^#]\S+\s+$BOOT\s| s|(\s)(defaults\S*)|\1\2,uid=$(id -u),gid=$(id -g),fmask=0133,dmask=0022|" /etc/fstab
  fi
  sudo systemctl daemon-reload
  sudo umount "$BOOT" && sudo mount "$BOOT"

  echo "==> Moving settings to $CFG"
  mkdir -p "$CFG_DIR"
  if [[ ! -f "$CFG" && -f "$OLD_CFG" ]]; then cp "$OLD_CFG" "$CFG"; fi
  sync

  echo "==> Pointing goal-light.service at the new settings file"
  sudo mkdir -p "$(dirname "$DROPIN")"
  printf '[Service]\nEnvironment=GOAL_LIGHT_CONFIG=%s\n' "$CFG" | sudo tee "$DROPIN" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl restart goal-light.service

  echo "==> Turning on the overlay file system (boot partition stays writable)"
  sudo raspi-config nonint do_overlayfs 0

  echo "==> Done. Reboot now: sudo reboot"
}

disable() {
  echo "==> Turning off the overlay file system"
  sudo raspi-config nonint do_overlayfs 1
  echo "==> Done. Reboot now: sudo reboot   (settings stay at $CFG)"
}

case "${1:-status}" in
  enable)  enable ;;
  disable) disable ;;
  status)  status ;;
  *) echo "usage: $0 enable|disable|status" >&2; exit 2 ;;
esac
