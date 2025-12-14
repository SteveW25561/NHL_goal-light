#!/usr/bin/env python3
"""
light.py

GPIO + Bud Light control helpers for the NHL goal light project.

- LIGHT_GPIO (BCM 23): drives the N2222 transistor that "presses" the Bud Light button.
- BUTTON_GPIO (BCM 22): reads the physical Bud Light button.

The Bud Light runs its LED cycle for ~6.1–6.2 seconds per button press.
We expose a helper that, given a desired *total* light-show
duration, will simulate repeated presses so that:
    - LEDs stay on continuously during the show
    - the final LED cycle ends about 1.5s after a given audio track ends
"""

import time
import threading

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:  # allows running on non-Pi for testing
    GPIO_AVAILABLE = False

# BCM pin numbers
LIGHT_GPIO = 23   # drives Bud Light button (via transistor)
BUTTON_GPIO = 22  # reads Bud Light's built-in button

# How long to hold the Bud Light button down for a "press"
PRESS_MS = 120

# Approximate LED run time per press, and desired overlap/end margin
# We schedule as if each spin is 6.0s so stacked presses overlap very slightly,
# avoiding tiny dark gaps between cycles even though the real spin is ~6.1–6.2s.
SPIN_PER_PRESS = 5.6      # seconds of light from a single physical press
END_MARGIN = 1.5          # seconds after audio end that we keep lights going

_button_callback = None
_button_enabled = False
_gpio_lock = threading.Lock()


def _log(msg: str) -> None:
    print(msg, flush=True)


def init_gpio(enable_button: bool) -> None:
    """Set up GPIO for light control and optional physical button."""
    global _button_enabled

    if not GPIO_AVAILABLE:
        _log("[LIGHT] GPIO not available; running in dummy mode.")
        return

    with _gpio_lock:
        GPIO.setmode(GPIO.BCM)

        # Output to press Bud Light button via transistor
        GPIO.setup(LIGHT_GPIO, GPIO.OUT, initial=GPIO.LOW)

        # Input from Bud Light's physical button (active LOW)
        GPIO.setup(BUTTON_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        _button_enabled = enable_button
        if enable_button:
            _enable_button_events_locked()
        else:
            _disable_button_events_locked()

        _log(
            f"[LIGHT] GPIO initialized (LIGHT_GPIO={LIGHT_GPIO}, "
            f"BUTTON_GPIO={BUTTON_GPIO}, button_enabled={_button_enabled})"
        )


def cleanup_gpio() -> None:
    """Clean up GPIO on shutdown."""
    if not GPIO_AVAILABLE:
        return
    with _gpio_lock:
        try:
            GPIO.cleanup()
        except Exception:
            pass
        _log("[LIGHT] GPIO cleaned up.")


def _enable_button_events_locked() -> None:
    """Enable edge detection on the physical Bud Light button."""
    global _button_enabled
    if not GPIO_AVAILABLE:
        return
    if _button_callback is None:
        return
    _button_enabled = True
    try:
        GPIO.remove_event_detect(BUTTON_GPIO)
    except Exception:
        pass
    GPIO.add_event_detect(
        BUTTON_GPIO,
        GPIO.FALLING,  # active-low button
        callback=lambda channel: _handle_button_edge(),
        bouncetime=80,
    )
    _log("[LIGHT] Physical button edge detection enabled (FALLING on BCM 22).")


def _disable_button_events_locked() -> None:
    global _button_enabled
    if not GPIO_AVAILABLE:
        return
    _button_enabled = False
    try:
        GPIO.remove_event_detect(BUTTON_GPIO)
    except Exception:
        pass
    _log("[LIGHT] Physical button edge detection disabled.")


def set_button_enabled(enabled: bool) -> None:
    """Turn physical button integration on/off at runtime."""
    if not GPIO_AVAILABLE:
        return
    with _gpio_lock:
        if enabled:
            _enable_button_events_locked()
        else:
            _disable_button_events_locked()


def register_button_callback(cb) -> None:
    """
    Register a callback called with no args when a *debounced* physical button
    press is detected. The callback is invoked in a background thread.
    """
    global _button_callback
    _button_callback = cb
    if not GPIO_AVAILABLE:
        return
    # Re-apply event detection if needed.
    with _gpio_lock:
        if _button_enabled:
            _enable_button_events_locked()


def _handle_button_edge() -> None:
    """Internal: invoked by GPIO when a button edge is detected."""
    if _button_callback is None:
        return
    t = threading.Thread(target=_button_callback, daemon=True)
    t.start()


def press_goal_button_once() -> None:
    """
    Simulate one Bud Light button press via the transistor.

    This does NOT enforce any lockouts; that's handled in nhl.py.
    """
    if not GPIO_AVAILABLE:
        _log("[LIGHT] Dummy press (GPIO unavailable).")
        time.sleep(PRESS_MS / 1000.0)
        return

    with _gpio_lock:
        GPIO.output(LIGHT_GPIO, GPIO.HIGH)
        time.sleep(PRESS_MS / 1000.0)
        GPIO.output(LIGHT_GPIO, GPIO.LOW)


def compute_light_schedule(audio_duration: float) -> list[float]:
    """
    Given an audio duration in seconds, return a list of press times (in seconds
    from start) such that:

      - LEDs are kept on continuously while the audio plays
      - the final LED cycle ends about END_MARGIN seconds after audio end

    Strategy:
      * target_total = max(audio_duration + END_MARGIN, SPIN_PER_PRESS)
      * After first press at t=0, each additional press extends lights
        by SPIN_PER_PRESS seconds.
      * We choose N = ceil(target_total / SPIN_PER_PRESS) presses.
      * These presses are spaced every SPIN_PER_PRESS, but shifted so that:
            t_last_press + SPIN_PER_PRESS ≈ target_total
        This avoids “dark” gaps in the middle of the song while keeping
        the final overshoot close to END_MARGIN.
    """
    if audio_duration <= 0:
        return [0.0]

    target_total = max(audio_duration + END_MARGIN, SPIN_PER_PRESS)

    # Minimum number of full cycles we need
    n_presses = int(-(-target_total // SPIN_PER_PRESS))  # ceil division
    if n_presses < 1:
        n_presses = 1

    total_press_span = (n_presses - 1) * SPIN_PER_PRESS
    # Shift so that last cycle end ≈ target_total
    shift = max(0.0, total_press_span + SPIN_PER_PRESS - target_total)

    press_times = []
    for i in range(n_presses):
        t = max(0.0, i * SPIN_PER_PRESS - shift)
        if not press_times or t - press_times[-1] >= 0.05:
            press_times.append(t)

    return press_times


def run_light_show_for_audio_duration(audio_duration: float, log_prefix: str) -> None:
    """
    Press the Bud Light button repeatedly so that the LEDs stay on for the
    entire audio_duration and end ≈ END_MARGIN seconds after.

    This is called from nhl.py in a background thread *while* the audio
    is playing, so we never block Flask or the main thread.
    """
    schedule = compute_light_schedule(audio_duration)
    if not schedule:
        return

    start = time.time()
    press_index = 0
    total_presses = len(schedule)

    while press_index < total_presses:
        now = time.time()
        elapsed = now - start
        next_press_at = schedule[press_index]

        if elapsed >= next_press_at:
            if press_index == 0:
                _log(
                    f"{log_prefix} press#1 at 0.00s "
                    f"(n_spins={total_presses}, target_total≈{audio_duration + END_MARGIN:.2f}s)"
                )
            else:
                _log(
                    f"{log_prefix} press#{press_index + 1} at≈{elapsed:.2f}s"
                )
            press_goal_button_once()
            press_index += 1
        else:
            time.sleep(min(0.05, next_press_at - elapsed))

    # Let last LED cycle end naturally; nothing more to do.