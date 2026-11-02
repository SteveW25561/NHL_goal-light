# NHL Goal Light

Raspberry Pi Zero 2 W controller that watches NHL games, fires a Bud Light goal light (GPIO 23 via transistor, button read on GPIO 22) and plays goal horns through a WM8960 sound card. Web UI on port 8080.

## Rebuild from scratch

1. **Flash the card** with Raspberry Pi Imager (use a high-endurance card, e.g. Samsung PRO Endurance or SanDisk High Endurance):

   | Imager screen | Setting |
   | --- | --- |
   | Device | Raspberry Pi Zero 2 W |
   | OS | Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)** |
   | Hostname | `goal-light` |
   | Localisation | Capital city **Ottawa (Canada)**, then time zone **America/Dawson_Creek**, keyboard `us` |
   | User | a username + password you will remember (there is no other way back in) |
   | Wi-Fi | a **2.4 GHz, WPA2** SSID (the Zero 2 W cannot join 5 GHz/6 GHz-only or WPA3-only networks), country **CA** |
   | Remote access | SSH on, password authentication |
   | Raspberry Pi Connect | off |

2. **Boot it** and give it a fixed IP / DHCP reservation in your router (anything pointing at it, e.g. a tunnel or bookmark, keeps working).
3. **SSH in and install** (one run, then reboot). Below, `USER` and `PI` are your username and the Pi's IP:
   ```bash
   ssh USER@PI
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/SteveW25561/NHL_goal-light.git ~/NHL_goal-light
   cd ~/NHL_goal-light && ./install.sh && sudo reboot
   ```
4. **Restore your settings** from the Mac, if you have a backup:
   ```bash
   scp ~/Downloads/goal-light-config.json USER@PI:~/goal_light/config.json
   ssh USER@PI sudo systemctl restart goal-light
   ```
   Otherwise open `http://PI:8080`, set teams/sounds, **Save Config**.

### Back up settings (from the Mac)

```bash
scp USER@PI:~/goal_light/config.json ~/Downloads/goal-light-config.json
```

`install.sh` links `~/goal_light` → the repo's `goal_light/` (the app expects that path), installs packages, enables the WM8960 overlay, sets the default ALSA card, allows passwordless `shutdown` for the web button, sets the time zone, and installs/enables `goal-light.service`. The service runs `audio-init.sh` before each start to unmute the WM8960 output.

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
