#!/usr/bin/env python3
"""
fetch_logos.py  -  Version 1.0.0 (2025-12-10)

One-time helper script to download NHL team logo SVGs into:
    ~/goal_light/static/logos/

Usage:
    cd ~/goal_light
    python3 fetch_logos.py
"""

import os
import sys
import time
from typing import List, Tuple

import requests

BASE_DIR = os.path.expanduser("~/goal_light")
LOGO_DIR = os.path.join(BASE_DIR, "static", "logos")

# Official-style NHL team abbreviations (32 teams)
NHL_TEAMS: List[Tuple[str, str]] = [
    ("ANA", "Anaheim Ducks"),
    ("ARI", "Arizona Coyotes"),
    ("BOS", "Boston Bruins"),
    ("BUF", "Buffalo Sabres"),
    ("CAR", "Carolina Hurricanes"),
    ("CBJ", "Columbus Blue Jackets"),
    ("CGY", "Calgary Flames"),
    ("CHI", "Chicago Blackhawks"),
    ("COL", "Colorado Avalanche"),
    ("DAL", "Dallas Stars"),
    ("DET", "Detroit Red Wings"),
    ("EDM", "Edmonton Oilers"),
    ("FLA", "Florida Panthers"),
    ("LAK", "Los Angeles Kings"),
    ("MIN", "Minnesota Wild"),
    ("MTL", "Montréal Canadiens"),
    ("NJD", "New Jersey Devils"),
    ("NSH", "Nashville Predators"),
    ("NYI", "New York Islanders"),
    ("NYR", "New York Rangers"),
    ("OTT", "Ottawa Senators"),
    ("PHI", "Philadelphia Flyers"),
    ("PIT", "Pittsburgh Penguins"),
    ("SEA", "Seattle Kraken"),
    ("SJS", "San Jose Sharks"),
    ("STL", "St. Louis Blues"),
    ("TBL", "Tampa Bay Lightning"),
    ("TOR", "Toronto Maple Leafs"),
    ("VAN", "Vancouver Canucks"),
    ("VGK", "Vegas Golden Knights"),
    ("WPG", "Winnipeg Jets"),
    ("WSH", "Washington Capitals"),
]

LOGO_URL_TEMPLATE = "https://assets.nhle.com/logos/nhl/svg/{abbr}_light.svg"


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_dirs() -> None:
    os.makedirs(LOGO_DIR, exist_ok=True)


def fetch_logo(abbr: str, name: str) -> bool:
    url = LOGO_URL_TEMPLATE.format(abbr=abbr)
    dest = os.path.join(LOGO_DIR, f"{abbr}_light.svg")

    if os.path.exists(dest):
        log(f"[SKIP] {abbr}: already exists at {dest}")
        return True

    try:
        log(f"[GET ] {abbr}: fetching {url}")
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "image/svg" not in content_type:
            log(f"[WARN] {abbr}: unexpected content type '{content_type}', saving anyway.")
        with open(dest, "wb") as f:
            f.write(resp.content)
        log(f"[OK  ] {abbr}: saved to {dest}")
        return True
    except Exception as e:
        log(f"[FAIL] {abbr}: error fetching logo for {name}: {e}")
        return False


def main() -> None:
    ensure_dirs()
    log(f"[INFO] Saving logos into: {LOGO_DIR}")
    success = 0
    fail = 0

    for abbr, name in NHL_TEAMS:
        if fetch_logo(abbr, name):
            success += 1
        else:
            fail += 1
        time.sleep(0.5)  # gentle pacing

    log(f"[DONE] Logos fetched. Success={success}, Fail={fail}")
    if fail > 0:
        log("       Some logos failed to download. You can retry running this script later.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n[ABORT] Interrupted by user.")
        sys.exit(1)
