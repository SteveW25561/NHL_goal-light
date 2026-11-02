#!/usr/bin/env bash
# audio-init.sh – unmute/set the WM8960 output path. Run by goal-light.service before the app starts.
# Never fails the service: if the card isn't there, the app still starts (just without sound).
CARD=wm8960soundcard
for _ in $(seq 1 20); do
  aplay -l 2>/dev/null | grep -q "$CARD" && break
  sleep 1
done
if ! aplay -l 2>/dev/null | grep -q "$CARD"; then
  echo "audio-init: $CARD not found, skipping mixer setup"
  exit 0
fi
amixer -c "$CARD" sset 'Left Output Mixer PCM' on  >/dev/null 2>&1 || true
amixer -c "$CARD" sset 'Right Output Mixer PCM' on >/dev/null 2>&1 || true
amixer -c "$CARD" sset 'Speaker' 90%   >/dev/null 2>&1 || true
amixer -c "$CARD" sset 'Headphone' 90% >/dev/null 2>&1 || true
echo "audio-init: $CARD mixer set"
exit 0
