#!/usr/bin/env python3
"""
nhl.py – NHL Goal Light controller + Web UI
Version: 1.9.11 (2025-12-13)

Delta from 1.9.10:
- Opponent Mode: manual Team 2 button dynamically shows actual opponent abbrev+logo (from best PRE/LIVE game)
- /status now returns team2_effective_abbr for UI
- Keep conservative schedule updates and gamecenter caching
"""

import os
import json
import time
import threading
import signal
from datetime import datetime, timedelta, timezone

# ---- XDG_RUNTIME_DIR fix (for pygame/SDL under systemd) ----
if not os.environ.get("XDG_RUNTIME_DIR"):
    try:
        uid = os.getuid()
        runtime_dir = f"/tmp/xdg-runtime-{uid}"
        os.makedirs(runtime_dir, exist_ok=True)
        try:
            os.chmod(runtime_dir, 0o700)
        except Exception:
            pass
        os.environ["XDG_RUNTIME_DIR"] = runtime_dir
    except Exception:
        pass

import requests
import pygame
from flask import Flask, render_template, request, redirect, url_for, jsonify

from light import init_gpio, cleanup_gpio, set_button_enabled, run_light_show_for_audio_duration

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# ---------------- Constants ----------------

APP_VERSION = "1.9.11"
UI_VERSION = "1.9.11"

BASE_DIR = os.path.expanduser("~/goal_light")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOGOS_DIR = os.path.join(STATIC_DIR, "logos")

# NHL endpoints
NHL_SCOREBOARD_URL = "https://api-web.nhle.com/v1/scoreboard/now"
NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/{date}"
NHL_GAMECENTER_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"

# Show lockout (lockout applies to web/auto; button bypass)
WEB_AUTO_LOCKOUT = 8.0

# Pi-only helper button (polled)
BUTTON_GPIO = 17          # BCM
BUTTON_LONG_PRESS = 1.2
BUTTON_POLL_HZ = 50

# Fallback lights duration when audio duration is 0/unknown
FALLBACK_DUR = 6.0

# Conservative caching for gamecenter period/clock
GAMECENTER_TTL_S = 12.0

# ---------------- Globals ----------------

stop_event = threading.Event()

config_lock = threading.Lock()
current_config = {}

show_lock = threading.Lock()
show_active = False
last_web_show = 0.0

# Active tracked games (PRE + LIVE candidates)
active_games = []
active_games_lock = threading.Lock()

# schedule cache
schedule_cache = {"ts": 0.0, "teams": None, "games": [], "last_label": ""}
schedule_lock = threading.Lock()

last_score_update = 0.0

# For auto-goal detection
score_state_lock = threading.Lock()
last_scores_by_game = {}  # gid -> {"home": int, "away": int}

# gamecenter cache: gid -> {"ts": float, "period_label": str, "clock": str}
gamecenter_cache_lock = threading.Lock()
gamecenter_cache = {}

# ---------------- pygame init ----------------
pygame.mixer.pre_init(44100, -16, 2, 1024)
pygame.init()
pygame.mixer.init()

# ---------------- Helpers ----------------

def log(msg: str):
    print(msg, flush=True)

def tzinfo(name: str):
    if ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc

def now_local_dt(tz_name: str) -> datetime:
    return datetime.now(tzinfo(tz_name))

def boolv(v) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")

def safe_float(v, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default

def safe_int(v, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _team_abbrev(team_obj) -> str:
    """Defensive extraction of team abbreviation across NHL API variants."""
    try:
        if isinstance(team_obj, dict):
            return (team_obj.get("abbrev")
                    or team_obj.get("teamAbbrev")
                    or team_obj.get("abbr")
                    or team_obj.get("triCode")
                    or "")
        if isinstance(team_obj, str):
            return team_obj
    except Exception:
        pass
    return ""

def fmt_time_local(dt: datetime, with_seconds: bool = True) -> str:
    if with_seconds:
        return dt.strftime("%H:%M:%S %Z")
    return dt.strftime("%H:%M %Z")

def fmt_game_start_local(dt: datetime) -> str:
    return dt.strftime("%I:%M %p %Z")

def compute_team2_effective_abbr(cfg: dict, active_list: list) -> str:
    """
    In normal mode: returns cfg.team2.
    In Opponent Mode (cfg.team2 == NONE): returns actual opponent abbrev if a PRE/LIVE tracked game exists.
    Uses the "best" game = first entry from active_list (already LIVE-first, then PRE).
    """
    team2 = (cfg.get("team2") or "NONE").upper()
    if team2 != "NONE":
        return team2

    team1 = (cfg.get("team1") or "VAN").upper()
    if not active_list:
        return "OPP"

    g = active_list[0] or {}
    away = (g.get("away") or "").upper()
    home = (g.get("home") or "").upper()
    if not away or not home:
        return "OPP"

    if team1 == away:
        return home or "OPP"
    if team1 == home:
        return away or "OPP"

    # Fallback: if somehow team1 doesn't match, still return the other side of the matchup
    return home or away or "OPP"

# ---------------- Config ----------------

DEFAULT_CONFIG = {
    "team1": "VAN",
    "team2": "NONE",

    "team1_sound": "None (lights only)",
    "team2_sound": "None (lights only)",

    "team1_volume": 90,
    "team2_volume": 70,

    "auto_goals_enabled": True,
    "stream_delay": 10.0,
    "poll_interval_seconds": 5.0,

    "timezone": "America/Vancouver",
    "physical_button_enabled": True,

    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
}

def _normalize_loaded_config(cfg: dict) -> dict:
    out = DEFAULT_CONFIG.copy()
    out.update(cfg or {})

    if "auto_goals" in out and "auto_goals_enabled" not in cfg:
        out["auto_goals_enabled"] = bool(out.get("auto_goals"))
    if "phys_button" in out and "physical_button_enabled" not in cfg:
        out["physical_button_enabled"] = bool(out.get("phys_button"))

    out["team1"] = (out.get("team1") or "VAN").upper()
    out["team2"] = (out.get("team2") or "NONE").upper()

    out["team1_volume"] = safe_int(out.get("team1_volume"), DEFAULT_CONFIG["team1_volume"])
    out["team2_volume"] = safe_int(out.get("team2_volume"), DEFAULT_CONFIG["team2_volume"])

    out["poll_interval_seconds"] = safe_float(out.get("poll_interval_seconds"), DEFAULT_CONFIG["poll_interval_seconds"])
    out["stream_delay"] = safe_float(out.get("stream_delay"), DEFAULT_CONFIG["stream_delay"])

    out["timezone"] = out.get("timezone") or DEFAULT_CONFIG["timezone"]

    return out

def load_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            cfg = {}
    return _normalize_loaded_config(cfg)

def save_config(cfg: dict):
    data = DEFAULT_CONFIG.copy()
    data.update(cfg or {})
    data["team1"] = (data.get("team1") or "VAN").upper()
    data["team2"] = (data.get("team2") or "NONE").upper()
    data["team1_volume"] = safe_int(data.get("team1_volume"), DEFAULT_CONFIG["team1_volume"])
    data["team2_volume"] = safe_int(data.get("team2_volume"), DEFAULT_CONFIG["team2_volume"])
    data["poll_interval_seconds"] = safe_float(data.get("poll_interval_seconds"), DEFAULT_CONFIG["poll_interval_seconds"])
    data["stream_delay"] = safe_float(data.get("stream_delay"), DEFAULT_CONFIG["stream_delay"])
    data["auto_goals_enabled"] = boolv(data.get("auto_goals_enabled"))
    data["physical_button_enabled"] = boolv(data.get("physical_button_enabled"))
    data["quiet_hours_enabled"] = boolv(data.get("quiet_hours_enabled"))

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log(f"[CONFIG] Failed to save config: {e}")

# ---------------- Quiet Hours ----------------

def quiet_active(cfg: dict) -> bool:
    if not boolv(cfg.get("quiet_hours_enabled")):
        return False
    try:
        sh, sm = map(int, str(cfg.get("quiet_hours_start", "22:00")).split(":"))
        eh, em = map(int, str(cfg.get("quiet_hours_end", "07:00")).split(":"))
    except Exception:
        return False

    now = now_local_dt(cfg.get("timezone") or DEFAULT_CONFIG["timezone"])
    cur = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em

    if start < end:
        return start <= cur < end
    return cur >= start or cur < end

# ---------------- Audio ----------------

def scan_sounds():
    if not os.path.isdir(SOUNDS_DIR):
        return []
    return sorted(
        f for f in os.listdir(SOUNDS_DIR)
        if f.lower().endswith((".mp3", ".wav", ".ogg"))
    )

def sound_mode(filename: str) -> str:
    s = (filename or "").strip().lower()
    if not s or s.startswith("none"):
        if "no lights" in s:
            return "NO_LIGHTS"
        return "LIGHTS_ONLY"
    return "AUDIO"

def play_sound(filename: str, volume: float) -> float:
    if sound_mode(filename) != "AUDIO":
        return 0.0

    path = os.path.join(SOUNDS_DIR, filename)
    if not os.path.exists(path):
        return 0.0

    try:
        snd = pygame.mixer.Sound(path)
        ch = snd.play()
        if ch:
            try:
                ch.set_volume(max(0.0, min(1.0, float(volume))))
            except Exception:
                pass
        return float(snd.get_length() or 0.0)
    except Exception:
        return 0.0

# ---------------- Light show ----------------

def trigger_show(team: int, source: str):
    global show_active, last_web_show

    now = time.time()
    is_button = str(source).startswith("BUTTON")

    with show_lock:
        if show_active:
            return
        if not is_button and now - last_web_show < WEB_AUTO_LOCKOUT:
            return
        show_active = True

    def run():
        global show_active, last_web_show
        try:
            with config_lock:
                cfg = current_config.copy()

            if quiet_active(cfg):
                dur = FALLBACK_DUR
                run_light_show_for_audio_duration(dur, f"[LIGHT T{team}]")
                time.sleep(dur)
                return

            if team == 1:
                selected = cfg.get("team1_sound", "")
                vol = float(cfg.get("team1_volume", 90)) / 100.0
            else:
                selected = cfg.get("team2_sound", "")
                vol = float(cfg.get("team2_volume", 70)) / 100.0

            mode = sound_mode(selected)
            if mode == "NO_LIGHTS":
                return

            dur = play_sound(selected, vol)
            if dur <= 0:
                dur = FALLBACK_DUR

            run_light_show_for_audio_duration(dur, f"[LIGHT T{team}]")
            time.sleep(dur)
        finally:
            with show_lock:
                show_active = False
                if not is_button:
                    last_web_show = time.time()

    threading.Thread(target=run, daemon=True).start()

# ---------------- NHL fetch helpers ----------------

def fetch_json(url: str) -> dict:
    try:
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

def _collect_games(container, out_list):
    if isinstance(container, dict):
        if "homeTeam" in container and "awayTeam" in container:
            out_list.append(container)
        for v in container.values():
            _collect_games(v, out_list)
    elif isinstance(container, list):
        for item in container:
            _collect_games(item, out_list)

def parse_games_for_teams(scoreboard: dict, team1: str, team2: str):
    tracked = []
    if not scoreboard:
        return tracked

    raw_games = []
    if isinstance(scoreboard, dict) and "games" in scoreboard:
        raw_games = list(scoreboard.get("games") or [])
    else:
        _collect_games(scoreboard, raw_games)

    t1 = (team1 or "VAN").upper()
    t2 = (team2 or "NONE").upper()

    seen = set()
    for g in raw_games:
        try:
            gid = int(g.get("id", 0) or 0)
            if not gid or gid in seen:
                continue
            seen.add(gid)

            home = _team_abbrev(g.get("homeTeam") or {}).upper()
            away = _team_abbrev(g.get("awayTeam") or {}).upper()
            if not home or not away:
                continue

            start_utc = g.get("startTimeUTC") or ""
            game_state = (g.get("gameState") or g.get("gameStateCode") or "OFF").upper()

            home_score = safe_int((g.get("homeTeam") or {}).get("score", 0), 0)
            away_score = safe_int((g.get("awayTeam") or {}).get("score", 0), 0)
        except Exception:
            continue

        if t2 == "NONE":
            is_tracked = (home == t1 or away == t1)
        else:
            is_tracked = (home in (t1, t2) or away in (t1, t2))

        if not is_tracked:
            continue

        tracked.append({
            "id": gid,
            "home": home,
            "away": away,
            "start_utc": start_utc,
            "game_state": game_state,
            "home_score": home_score,
            "away_score": away_score,
        })

    return tracked

def fetch_period_and_clock_uncached(game_id: int):
    data = fetch_json(NHL_GAMECENTER_URL.format(game_id=game_id))
    if not data:
        return "", ""

    try:
        clock_raw = data.get("clock") or ""
        pd = data.get("periodDescriptor") or {}
        number = pd.get("number")
        ptype = (pd.get("periodType") or pd.get("type") or "").upper()

        if ptype in ("OT", "SO", "SHOOTOUT"):
            period_label = "SO" if ptype == "SHOOTOUT" else ptype
        else:
            period_label = f"P{number}" if isinstance(number, int) and number in (1, 2, 3) else ""

        clock_str = ""
        if isinstance(clock_raw, dict):
            intermission = bool(clock_raw.get("intermission") or clock_raw.get("inIntermission") or False)
            time_remaining = clock_raw.get("timeRemaining") or clock_raw.get("timeRemainingExact") or ""
            if intermission:
                period_label = "INT"
                clock_str = ""
            else:
                clock_str = str(time_remaining) if time_remaining else ""
        else:
            clock_str = str(clock_raw)

        return period_label, clock_str
    except Exception:
        return "", ""

def fetch_period_and_clock_cached(game_id: int):
    now = time.time()
    with gamecenter_cache_lock:
        ent = gamecenter_cache.get(game_id)
        if ent and (now - float(ent.get("ts", 0.0))) < GAMECENTER_TTL_S:
            return ent.get("period_label", ""), ent.get("clock", "")

    pl, ck = fetch_period_and_clock_uncached(game_id)

    with gamecenter_cache_lock:
        gamecenter_cache[game_id] = {"ts": now, "period_label": pl, "clock": ck}

    return pl, ck

# ---------------- Auto-goal detection ----------------

def _maybe_trigger_auto_goals(cfg: dict, tracked_games: list):
    team1 = (cfg.get("team1") or "VAN").upper()
    team2 = (cfg.get("team2") or "NONE").upper()
    autos = boolv(cfg.get("auto_goals_enabled"))
    delay = safe_float(cfg.get("stream_delay"), 10.0)

    live_states = {"LIVE", "CRIT", "OT", "SO", "SHOOTOUT"}
    if not autos:
        return

    for rec in tracked_games:
        gid = safe_int(rec.get("id"), 0)
        if gid <= 0:
            continue

        state = (rec.get("game_state") or "OFF").upper()
        if state not in live_states:
            continue

        home = (rec.get("home") or "").upper()
        away = (rec.get("away") or "").upper()
        home_score = safe_int(rec.get("home_score"), 0)
        away_score = safe_int(rec.get("away_score"), 0)

        with score_state_lock:
            prev = last_scores_by_game.get(gid)
            if prev is None:
                last_scores_by_game[gid] = {"home": home_score, "away": away_score}
                continue

            dh = max(0, home_score - safe_int(prev.get("home"), 0))
            da = max(0, away_score - safe_int(prev.get("away"), 0))
            last_scores_by_game[gid] = {"home": home_score, "away": away_score}

        goals_t1 = 0
        goals_t2 = 0

        if home == team1:
            goals_t1 = dh
        elif away == team1:
            goals_t1 = da

        if team2 != "NONE":
            if home == team2:
                goals_t2 = dh
            elif away == team2:
                goals_t2 = da
        else:
            if home == team1:
                goals_t2 = da
            elif away == team1:
                goals_t2 = dh

        def _delayed_fire(team_idx: int, count: int):
            def _one():
                if delay and delay > 0:
                    time.sleep(delay)
                trigger_show(team_idx, f"AUTO-NHL-T{team_idx}")
            for _ in range(count):
                threading.Thread(target=_one, daemon=True).start()

        if goals_t1 > 0:
            log(f"[NHL] Auto-goal: {goals_t1} for Team1({team1}) gid={gid}")
            _delayed_fire(1, goals_t1)

        if goals_t2 > 0:
            label = team2 if team2 != "NONE" else "OPP"
            log(f"[NHL] Auto-goal: {goals_t2} for Team2/OPP({label}) gid={gid}")
            _delayed_fire(2, goals_t2)

# ---------------- Scoreboard poller ----------------

def scoreboard_poller():
    global active_games, last_score_update

    LIVE_STATES = {"LIVE", "CRIT", "OT", "SO", "SHOOTOUT"}
    CANDIDATE_STATES = {"PRE"} | LIVE_STATES

    while not stop_event.is_set():
        with config_lock:
            cfg = current_config.copy()

        sb = fetch_json(NHL_SCOREBOARD_URL)
        if sb:
            last_score_update = time.time()

        team1 = (cfg.get("team1") or "VAN").upper()
        team2 = (cfg.get("team2") or "NONE").upper()

        tracked = parse_games_for_teams(sb, team1, team2)

        try:
            _maybe_trigger_auto_goals(cfg, tracked)
        except Exception as e:
            log(f"[NHL] Auto-goal detection error: {e}")

        candidates = []
        any_live = False

        for rec in tracked:
            state = (rec.get("game_state") or "OFF").upper()
            if state not in CANDIDATE_STATES:
                continue

            gid = safe_int(rec.get("id"), 0)
            is_live = state in LIVE_STATES
            is_pre = state == "PRE"

            period_label = ""
            clock = ""
            if is_live and gid:
                period_label, clock = fetch_period_and_clock_cached(gid)
                any_live = True

            out = dict(rec)
            out["is_live"] = is_live
            out["is_pre"] = is_pre
            out["period_label"] = period_label
            out["clock"] = clock

            candidates.append(out)

        def _sort_key(g):
            state = (g.get("game_state") or "").upper()
            live_rank = 0 if state in LIVE_STATES else 1
            try:
                st = g.get("start_utc") or ""
                dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
                return (live_rank, dt)
            except Exception:
                return (live_rank, datetime.max)

        candidates.sort(key=_sort_key)

        with active_games_lock:
            active_games = candidates

        live_interval = safe_float(cfg.get("poll_interval_seconds"), 5.0)
        sleep_s = live_interval if any_live else 60.0

        end = time.time() + max(5.0, sleep_s)
        while time.time() < end and not stop_event.is_set():
            time.sleep(0.25)

# ---------------- Schedule ----------------

def _collect_schedule_for_next_35_days(cfg: dict):
    tz_name = cfg.get("timezone") or DEFAULT_CONFIG["timezone"]
    tz_obj = tzinfo(tz_name)
    today_local = now_local_dt(tz_name).date()

    team1 = (cfg.get("team1") or "VAN").upper()
    team2 = (cfg.get("team2") or "NONE").upper()

    out = []
    fetched_weeks = 0

    for i in range(0, 35, 7):
        if stop_event.is_set():
            break
        d = (today_local + timedelta(days=i)).isoformat()
        data = fetch_json(NHL_SCHEDULE_URL.format(date=d))
        fetched_weeks += 1

        weeks = data.get("gameWeek") or []
        for day in weeks:
            games = day.get("games") or []
            for g in games:
                try:
                    gid = safe_int(g.get("id") or g.get("gameId") or 0, 0)
                    home = _team_abbrev(g.get("homeTeam") or {}).upper()
                    away = _team_abbrev(g.get("awayTeam") or {}).upper()
                    if not home or not away:
                        continue
                except Exception:
                    continue

                involves_t1 = team1 in (home, away)
                involves_t2 = (team2 != "NONE" and team2 in (home, away))
                involves_opp_mode = (team2 == "NONE" and involves_t1)

                if not (involves_t1 or involves_t2 or involves_opp_mode):
                    continue

                start_utc = g.get("startTimeUTC") or ""
                state = (g.get("gameState") or g.get("gameStateCode") or "").upper()

                time_local_str = ""
                try:
                    dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
                    dt_local = dt.astimezone(tz_obj)
                    time_local_str = dt_local.strftime("%a %b %d, %I:%M %p %Z")
                except Exception:
                    time_local_str = start_utc

                hs = (g.get("homeTeam") or {}).get("score")
                aws = (g.get("awayTeam") or {}).get("score")

                is_live = state in ("LIVE", "CRIT", "OT", "SO", "SHOOTOUT")
                is_pre = state == "PRE"

                out.append({
                    "id": gid,
                    "time_local": time_local_str,
                    "matchup": f"{away} @ {home}",
                    "state": state or "—",
                    "away_score": 0 if aws is None else aws,
                    "home_score": 0 if hs is None else hs,
                    "away": away,
                    "home": home,
                    "row_class": "live" if is_live else ("pre" if is_pre else ""),
                    "is_active": False,
                })

    seen = set()
    deduped = []
    for g in out:
        key = g.get("id") or (g.get("time_local"), g.get("matchup"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(g)

    label = f"Fetched {len(deduped)} games (≈{fetched_weeks} schedule calls)."
    return deduped, label

def get_cached_schedule(cfg: dict, active_list: list):
    team_key = (cfg.get("team1"), cfg.get("team2"), cfg.get("timezone"))

    with schedule_lock:
        fresh = (time.time() - float(schedule_cache["ts"] or 0.0)) < 600
        same = (schedule_cache["teams"] == team_key)
        if fresh and same and schedule_cache["games"]:
            games = list(schedule_cache["games"])
            label = schedule_cache.get("last_label", "")
        else:
            games, label = _collect_schedule_for_next_35_days(cfg)
            schedule_cache["ts"] = time.time()
            schedule_cache["teams"] = team_key
            schedule_cache["games"] = list(games)
            schedule_cache["last_label"] = label

    active_ids = set()
    for ag in active_list or []:
        gid = safe_int(ag.get("id"), 0)
        if gid:
            active_ids.add(gid)

    if active_ids:
        for g in games:
            gid = safe_int(g.get("id"), 0)
            g["is_active"] = (gid in active_ids)

    return games, label

# ---------------- Button poller ----------------

def button_poller():
    try:
        import RPi.GPIO as GPIO
    except Exception:
        return

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    last = 1
    press_start = None

    while not stop_event.is_set():
        val = GPIO.input(BUTTON_GPIO)
        if val == 0 and last == 1:
            press_start = time.time()
        if val == 1 and last == 0:
            dur = time.time() - press_start if press_start else 0
            if dur >= BUTTON_LONG_PRESS:
                trigger_show(2, "BUTTON-LONG")
            else:
                trigger_show(1, "BUTTON-SHORT")
        last = val
        time.sleep(1 / BUTTON_POLL_HZ)

# ---------------- Flask ----------------

app = Flask(
    __name__,
    static_folder=STATIC_DIR,
    template_folder=os.path.join(BASE_DIR, "templates"),
)

TEAMS = [
    ("ANA","Anaheim Ducks"),("ARI","Arizona Coyotes"),("BOS","Boston Bruins"),
    ("BUF","Buffalo Sabres"),("CGY","Calgary Flames"),("CAR","Carolina Hurricanes"),
    ("CHI","Chicago Blackhawks"),("COL","Colorado Avalanche"),("CBJ","Columbus Blue Jackets"),
    ("DAL","Dallas Stars"),("DET","Detroit Red Wings"),("EDM","Edmonton Oilers"),
    ("FLA","Florida Panthers"),("LAK","Los Angeles Kings"),("MIN","Minnesota Wild"),
    ("MTL","Montréal Canadiens"),("NSH","Nashville Predators"),("NJD","New Jersey Devils"),
    ("NYI","New York Islanders"),("NYR","New York Rangers"),("OTT","Ottawa Senators"),
    ("PHI","Philadelphia Flyers"),("PIT","Pittsburgh Penguins"),("SJS","San Jose Sharks"),
    ("SEA","Seattle Kraken"),("STL","St. Louis Blues"),("TBL","Tampa Bay Lightning"),
    ("TOR","Toronto Maple Leafs"),("VAN","Vancouver Canucks"),("VGK","Vegas Golden Knights"),
    ("WPG","Winnipeg Jets"),("WSH","Washington Capitals"),
]

TIMEZONES = [
    ("America/Vancouver","Pacific"),
    ("America/Edmonton","Mountain"),
    ("America/Winnipeg","Central"),
    ("America/Toronto","Eastern"),
]

def build_banners(cfg: dict, games_list: list):
    tz_name = cfg.get("timezone") or DEFAULT_CONFIG["timezone"]
    tz_obj = tzinfo(tz_name)

    now_loc = now_local_dt(tz_name)
    asof_local = fmt_time_local(now_loc, with_seconds=True)

    banners = []
    for g in (games_list or []):
        away = (g.get("away") or "").upper()
        home = (g.get("home") or "").upper()

        is_live = bool(g.get("is_live"))
        is_pre = bool(g.get("is_pre"))

        if is_live:
            color = "green"
        elif is_pre:
            color = "yellow"
        else:
            color = "red"

        period_label = (g.get("period_label") or "").strip()
        clock = (g.get("clock") or "").strip()
        extra = ""
        if period_label and clock:
            extra = f"{period_label}*{clock}"
        elif period_label:
            extra = period_label
        elif clock:
            extra = clock

        away_score = safe_int(g.get("away_score"), 0)
        home_score = safe_int(g.get("home_score"), 0)

        start_local_str = ""
        try:
            st = (g.get("start_utc") or "").strip()
            if st:
                dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
                dt_local = dt.astimezone(tz_obj)
                start_local_str = f"game start {fmt_game_start_local(dt_local)}"
        except Exception:
            start_local_str = ""

        banners.append({
            "id": safe_int(g.get("id"), 0),
            "away": away,
            "home": home,
            "away_score": away_score,
            "home_score": home_score,
            "banner_color": color,
            "is_live": is_live,
            "is_pre": is_pre,
            "extra": extra,
            "start_local": start_local_str,
            "asof": asof_local,
            "away_logo": f"{away}_light.svg" if away else "",
            "home_logo": f"{home}_light.svg" if home else "",
        })

    return banners, asof_local, now_loc

@app.route("/")
def index():
    with config_lock:
        cfg = current_config.copy()

    tz_name = cfg.get("timezone") or DEFAULT_CONFIG["timezone"]
    now = now_local_dt(tz_name)

    with active_games_lock:
        ag = list(active_games)

    games, sched_label = get_cached_schedule(cfg, ag)

    return render_template(
        "index.html",
        cfg=cfg,
        teams=TEAMS,
        timezones=TIMEZONES,
        sound_files=scan_sounds(),
        schedule_label=sched_label,
        games=games,
        host=os.uname().nodename,
        app_version=APP_VERSION,
        ui_version=UI_VERSION,
        current_time=now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        quiet_active=quiet_active(cfg),
    )

@app.route("/refresh_schedule", methods=["POST"])
def refresh_schedule():
    with schedule_lock:
        schedule_cache["ts"] = 0.0
    return redirect(url_for("index"))

@app.route("/config", methods=["POST"])
def config():
    with config_lock:
        for k in DEFAULT_CONFIG:
            if k in request.form:
                current_config[k] = request.form[k]

        current_config["auto_goals_enabled"] = ("auto_goals_enabled" in request.form)
        current_config["physical_button_enabled"] = ("physical_button_enabled" in request.form)
        current_config["quiet_hours_enabled"] = ("quiet_hours_enabled" in request.form)

        current_config["team1"] = (current_config.get("team1") or "VAN").upper()
        current_config["team2"] = (current_config.get("team2") or "NONE").upper()
        current_config["team1_volume"] = safe_int(current_config.get("team1_volume"), DEFAULT_CONFIG["team1_volume"])
        current_config["team2_volume"] = safe_int(current_config.get("team2_volume"), DEFAULT_CONFIG["team2_volume"])
        current_config["poll_interval_seconds"] = safe_float(current_config.get("poll_interval_seconds"), DEFAULT_CONFIG["poll_interval_seconds"])
        current_config["stream_delay"] = safe_float(current_config.get("stream_delay"), DEFAULT_CONFIG["stream_delay"])
        current_config["timezone"] = current_config.get("timezone") or DEFAULT_CONFIG["timezone"]

        save_config(current_config)

        with schedule_lock:
            schedule_cache["ts"] = 0.0

    return redirect(url_for("index"))

@app.route("/trigger_team1", methods=["POST"])
def trigger_team1():
    trigger_show(1, "WEB-T1")
    return redirect(url_for("index"))

@app.route("/trigger_team2", methods=["POST"])
def trigger_team2():
    trigger_show(2, "WEB-T2")
    return redirect(url_for("index"))

@app.route("/status")
def status():
    with config_lock:
        cfg = current_config.copy()

    with active_games_lock:
        ag = list(active_games)

    banners, asof_local, now_loc = build_banners(cfg, ag)

    current_time_local = now_loc.strftime("%Y-%m-%d %H:%M:%S %Z")
    quiet_line = f"{'ON (audio muted)' if quiet_active(cfg) else 'OFF'} · as of {asof_local}"

    asof_utc = ""
    if last_score_update:
        asof_utc = datetime.fromtimestamp(last_score_update, tz=timezone.utc).isoformat()

    team2_effective = compute_team2_effective_abbr(cfg, ag)

    return jsonify({
        "backend_version": APP_VERSION,
        "ui_version": UI_VERSION,
        "banners": banners,
        "asof_local": asof_local,
        "current_time_local": current_time_local,
        "quiet_line": quiet_line,
        "active_games": ag,
        "asof_utc": asof_utc,
        "team1": cfg.get("team1"),
        "team2": cfg.get("team2"),
        "team2_effective_abbr": team2_effective,
    })

# ---------------- Shutdown handling ----------------

def _shutdown(signum=None, frame=None):
    try:
        log(f"[MAIN] Shutdown requested (signal={signum}).")
    except Exception:
        pass
    stop_event.set()
    try:
        cleanup_gpio()
    except Exception:
        pass
    os._exit(0)

# ---------------- Main ----------------

def main():
    global current_config

    os.makedirs(BASE_DIR, exist_ok=True)

    current_config = load_config()
    log(f"[MAIN] Starting NHL Goal Light v{APP_VERSION} host={os.uname().nodename}")
    log(f"[CONFIG] Loaded: {current_config}")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    init_gpio(False)
    set_button_enabled(False)

    if boolv(current_config.get("physical_button_enabled")):
        threading.Thread(target=button_poller, daemon=True).start()

    threading.Thread(target=scoreboard_poller, daemon=True).start()

    app.run(host="0.0.0.0", port=8080, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
    