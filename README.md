# NHL Goal Light

Raspberry Pi Zero 2 W controller that watches NHL games, fires a Bud Light goal light (GPIO 23 via transistor, button read on GPIO 22) and plays goal horns through a WM8960 sound card. Web UI on port 8080.

## Rebuild from scratch

1. **Flash the card** with Raspberry Pi Imager:
   - Device: Raspberry Pi Zero 2 W
   - OS: Raspberry Pi OS Lite (64-bit)
   - Settings: hostname `pi-goal`, your username/password, SSH on, Wi-Fi **YOUR-2.4GHZ-SSID** (the Zero 2 W is 2.4 GHz / WPA2 only), country CA.
   - Use a high-endurance card (Samsung PRO Endurance, SanDisk High Endurance).
2. **Give it a fixed IP** (PI) in UniFi.
3. **SSH in and install:**
   ```bash
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/SteveW25561/NHL_goal-light.git ~/NHL_goal-light
   cd ~/NHL_goal-light && ./install.sh
   sudo reboot
   # after it comes back:
   cd ~/NHL_goal-light && ./install.sh   # second run sets the sound card mixer
   ```
4. Open `http://PI:8080`, set teams/sounds, **Save Config**.

`install.sh` links `~/goal_light` → the repo's `goal_light/` (the app expects that path), installs packages, enables the WM8960 overlay, sets the default ALSA card, allows passwordless `shutdown` for the web button, sets the time zone, and installs/enables `goal-light.service`.

## Day-to-day

```bash
sudo systemctl status goal-light      # is it running?
journalctl -u goal-light -f           # live log
cd ~/NHL_goal-light && git pull && sudo systemctl restart goal-light   # update
```

`config.json` (your settings) lives in `goal_light/` and is git-ignored.

## Time zone

BC moved to permanent UTC-7 in Nov 2026; older tzdata still switches `America/Vancouver` to PST. The installer and the app default to `America/Dawson_Creek` (fixed UTC-7). Once Debian's tzdata catches up you can pick "Pacific" again in the UI.

## Security

The web UI has no login, and `/api/shutdown` powers off the Pi. If it is exposed through Cloudflare Tunnel, put it behind Cloudflare Access.
