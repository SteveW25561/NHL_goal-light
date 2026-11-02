#!/usr/bin/env bash
# readonly.sh – run the Pi with a read-only root filesystem (overlay), so pulling
# the plug can't corrupt the SD card. Settings (config.json) move to the boot
# partition, which stays writable and is only written when you click Save Config.
#
#   ./readonly.sh enable    # move settings, turn overlay on, reboot
#   ./readonly.sh update    # pull + reinstall from GitHub, then back to read-only (reboots twice, hands-off)
#   ./readonly.sh disable   # turn overlay off for manual changes (apt upgrade, Wi-Fi), reboot
#   ./readonly.sh status
#
# While enabled, any change outside the boot partition (git pull, apt, edits in ~)
# is thrown away at the next reboot.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${APP_USER:-$(id -un)}"
BOOT=/boot/firmware
CFG_DIR="$BOOT/goal-light"
CFG="$CFG_DIR/config.json"
OLD_CFG="$HOME/goal_light/config.json"
DROPIN=/etc/systemd/system/goal-light.service.d/config-path.conf
FLAG="$CFG_DIR/pending-update"
LOG="$CFG_DIR/last-update.log"

# raspi-config's non-interactive do_overlayfs also write-protects the boot partition
# (it adds ",ro" to the /boot/firmware fstab line). Undo that so Save Config works.
# Only works while the root is writable (before the overlay is active).
boot_fstab_rw() {
  sed -i -E "\|^[^#]\S+\s+$BOOT\s| s/,ro([,[:space:]])/\1/g" /etc/fstab
}

overlay_on() { grep -q "overlayroot=tmpfs" "$BOOT/cmdline.txt" 2>/dev/null; }

status() {
  if overlay_on; then echo "overlay: ENABLED in cmdline.txt (read-only root after reboot)"; else echo "overlay: disabled"; fi
  if mount | grep -q " on / type overlay"; then echo "root right now: read-only overlay"; else echo "root right now: normal (writable)"; fi
  if [[ -f "$DROPIN" ]]; then echo "settings file: $CFG"; else echo "settings file: $OLD_CFG"; fi
  if mount | grep -E " on $BOOT .*\((ro|ro,)" >/dev/null; then echo "boot partition: READ-ONLY (Save Config will NOT persist)"; else echo "boot partition: writable"; fi
  if [[ -f "$LOG" ]]; then echo "last update:"; tail -n 3 "$LOG" | sed 's/^/  /'; fi
}

root_is_overlay() { mount | grep -q " on / type overlay"; }

update() {
  [[ $EUID -ne 0 ]] || { echo "Run as your normal user." >&2; exit 1; }
  if root_is_overlay; then
    echo "==> Read-only mode is on. Scheduling: reboot writable -> git pull + install.sh -> read-only -> reboot"
    mkdir -p "$CFG_DIR"
    touch "$FLAG"
    sync
    sudo raspi-config nonint do_overlayfs 1
    echo "==> Rebooting. The Pi will reboot once more by itself; allow ~3-5 minutes."
    echo "    Check afterwards with: $REPO_DIR/readonly.sh status"
    sudo reboot
  else
    echo "==> Root is writable: updating in place"
    git -C "$REPO_DIR" pull --ff-only
    "$REPO_DIR/install.sh"
    sudo systemctl restart goal-light.service
    echo "==> Updated. (Read-only mode is not active; './readonly.sh enable' to turn it on.)"
  fi
}

# Run as root by goal-light-update.service at boot when $FLAG exists.
_worker() {
  [[ $EUID -eq 0 ]] || { echo "_worker must run as root" >&2; exit 1; }
  rm -f "$FLAG"; sync
  if root_is_overlay; then
    echo "$(date '+%F %T') update skipped: root still read-only" >> "$LOG"
    exit 0
  fi
  {
    echo "$(date '+%F %T') update started"
    ok=0
    for i in 1 2 3 4 5; do
      if runuser -u "$APP_USER" -- git -C "$REPO_DIR" pull --ff-only; then ok=1; break; fi
      sleep 15
    done
    if [[ $ok -eq 1 ]] && APP_USER="$APP_USER" "$REPO_DIR/install.sh"; then
      echo "$(date '+%F %T') update OK: $(runuser -u "$APP_USER" -- git -C "$REPO_DIR" log --oneline -1)"
    else
      echo "$(date '+%F %T') update FAILED (see above); returning to read-only anyway"
    fi
  } >> "$LOG" 2>&1
  raspi-config nonint do_overlayfs 0 >> "$LOG" 2>&1 || true
  boot_fstab_rw
  sync
  systemctl reboot
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
  sudo bash -c "$(declare -f boot_fstab_rw); BOOT=$BOOT; boot_fstab_rw"
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
  sudo bash -c "$(declare -f boot_fstab_rw); BOOT=$BOOT; boot_fstab_rw"
  if awk -v b="$BOOT" '$2==b && $4 ~ /(^|,)ro(,|$)/ {f=1} END {exit !f}' /etc/fstab; then
    echo "WARNING: $BOOT is still read-only in /etc/fstab; Save Config would not persist." >&2
    exit 1
  fi

  echo "==> Done. Reboot now: sudo reboot"
}

disable() {
  echo "==> Turning off the overlay file system"
  sudo raspi-config nonint do_overlayfs 1
  echo "==> Done. Reboot now: sudo reboot   (settings stay at $CFG)"
}

case "${1:-status}" in
  enable)  enable ;;
  update)  update ;;
  _worker) _worker ;;
  disable) disable ;;
  status)  status ;;
  *) echo "usage: $0 enable|disable|update|status" >&2; exit 2 ;;
esac
