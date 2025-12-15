import unittest
import sys
import os
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Provide lightweight stubs to satisfy imports without external dependencies.
if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *args, **kwargs: None)
if "pygame" not in sys.modules:
    class _DummyMixer:
        def pre_init(self, *args, **kwargs):
            return None

        def init(self, *args, **kwargs):
            return None

        def Sound(self, *args, **kwargs):
            return types.SimpleNamespace(play=lambda *a, **k: None, get_length=lambda: 0.0)

        music = types.SimpleNamespace(load=lambda *a, **k: None, play=lambda *a, **k: None, stop=lambda *a, **k: None, set_volume=lambda *a, **k: None)

    sys.modules["pygame"] = types.SimpleNamespace(mixer=_DummyMixer(), init=lambda *a, **k: None)
if "flask" not in sys.modules:
    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def run(self, *args, **kwargs):
            return None

    def _jsonify(obj=None, **kwargs):
        if obj is None:
            obj = {}
        obj.update(kwargs)
        return obj

    sys.modules["flask"] = types.SimpleNamespace(
        Flask=_DummyFlask,
        render_template=lambda *args, **kwargs: None,
        request=types.SimpleNamespace(form={}, get_json=lambda *a, **k: {}),
        redirect=lambda *args, **kwargs: None,
        url_for=lambda *args, **kwargs: "",
        jsonify=_jsonify,
    )
if "light" not in sys.modules:
    sys.modules["light"] = types.SimpleNamespace(
        cleanup_gpio=lambda *a, **k: None,
        init_gpio=lambda *a, **k: None,
        press_goal_button_once=lambda *a, **k: None,
        run_light_show_for_audio_duration=lambda *a, **k: None,
        set_button_enabled=lambda *a, **k: None,
    )

from goal_light import nhl


class StreamDelayTests(unittest.TestCase):
    def test_parse_clock_valid(self):
        self.assertEqual(nhl.parse_clock_to_seconds("12:34"), 754)
        self.assertEqual(nhl.parse_clock_to_seconds("5:07"), 307)
        self.assertEqual(nhl.parse_clock_to_seconds("07"), 420)

    def test_parse_clock_invalid(self):
        self.assertIsNone(nhl.parse_clock_to_seconds(""))
        self.assertIsNone(nhl.parse_clock_to_seconds("12:99"))
        self.assertIsNone(nhl.parse_clock_to_seconds("abc"))

    def test_compute_stream_lag_seconds(self):
        lag, capped = nhl.compute_stream_lag_seconds("10:00", "09:30")
        self.assertAlmostEqual(lag, 30.0)
        self.assertFalse(capped)

        lag, capped = nhl.compute_stream_lag_seconds("12:00", "10:30", cap_seconds=60)
        self.assertAlmostEqual(lag, 60.0 - 0.0)
        self.assertTrue(capped)

        lag, capped = nhl.compute_stream_lag_seconds("08:00", "08:30")
        self.assertEqual(lag, 0.0)
        self.assertFalse(capped)

        lag, capped = nhl.compute_stream_lag_seconds("bad", "08:30")
        self.assertIsNone(lag)
        self.assertFalse(capped)


if __name__ == "__main__":
    unittest.main()
