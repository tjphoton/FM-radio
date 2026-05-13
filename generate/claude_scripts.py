"""
Generates content specs (music prompts, DJ intros, talk show scripts) by calling
the `claude` CLI. Uses the Claude Code subscription — no API key required.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_config() -> dict:
    with open(REPO_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def fetch_weather(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current_weather=true"
        f"&temperature_unit=celsius"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        cw = resp.json().get("current_weather", {})
        return {
            "temp": cw.get("temperature", 15),
            "code": cw.get("weathercode", 0),
            "description": _weather_description(cw.get("weathercode", 0)),
        }
    except Exception as exc:
        log.warning("Weather fetch failed (%s) — using defaults", exc)
        return {"temp": 15, "code": 0, "description": "clear skies"}


def _weather_description(code: int) -> str:
    ranges = [
        (range(0, 1),   "clear skies"),
        (range(1, 4),   "partly cloudy"),
        (range(45, 50), "foggy"),
        (range(51, 68), "light rain"),
        (range(71, 78), "light snow"),
        (range(80, 83), "rain showers"),
        (range(95, 100),"thunderstorm"),
    ]
    for r, desc in ranges:
        if code in r:
            return desc
    return "overcast"


# ---------------------------------------------------------------------------
# Time context
# ---------------------------------------------------------------------------

def time_context(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    hour = now.hour
    month = now.month
    dow = now.strftime("%A")

    if 6 <= hour < 12:
        tod = "morning"
    elif 12 <= hour < 17:
        tod = "afternoon"
    elif 17 <= hour < 22:
        tod = "evening"
    else:
        tod = "night"

    if month in (12, 1, 2):
        season = "winter"
    elif month in (3, 4, 5):
        season = "spring"
    elif month in (6, 7, 8):
        season = "summer"
    else:
        season = "autumn"

    return {
        "hour": hour,
        "time_of_day": tod,
        "season": season,
        "month": month,
        "day_of_week": dow,
        "date": now.strftime("%Y-%m-%d"),
    }


def current_show_block(now: datetime | None = None) -> str:
    now = now or datetime.now()
    hour = now.hour
    dow = now.weekday()  # 0=Monday

    if dow == 0 and hour == 19:
        return "meditation_mondays"
    if dow in (1, 2, 3, 4) and hour == 17:
        return "jazz_at_dusk"
    if dow == 6 and hour == 8:
        return "sunday_folk"

    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


# ---------------------------------------------------------------------------
# claude CLI call
# ---------------------------------------------------------------------------

def _call_claude(prompt: str, timeout: int = 120) -> str:
    """Calls `claude -p <prompt> --output-format text` and returns stdout."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _render_template(template_path: Path, vars: dict[str, Any]) -> str:
    text = template_path.read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", str(value))
    return text


# ---------------------------------------------------------------------------
# Music prompts
# ---------------------------------------------------------------------------

def generate_music_prompts(
    cfg: dict,
    weather: dict,
    ctx: dict,
    show_block: str,
    count: int = 10,
) -> list[dict]:
    station = cfg["station"]
    dj = cfg["dj"]
    template_vars = {
        "station_name": station["name"],
        "tagline": station["tagline"],
        "dj_name": dj["name"],
        "dj_persona": dj["persona"].strip(),
        "count": count,
        "time_of_day": ctx["time_of_day"],
        "hour": ctx["hour"],
        "season": ctx["season"],
        "month": ctx["month"],
        "day_of_week": ctx["day_of_week"],
        "weather_description": weather["description"],
        "weather_temp": weather["temp"],
        "show_block": show_block,
    }
    prompt = _render_template(PROMPTS_DIR / "music_prompt.txt", template_vars)
    raw = _call_claude(prompt)

    try:
        prompts = json.loads(raw)
        if not isinstance(prompts, list):
            raise ValueError("Expected JSON array")
        return prompts
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse music prompts JSON: %s\nRaw: %s", exc, raw[:500])
        return []


# ---------------------------------------------------------------------------
# DJ intros
# ---------------------------------------------------------------------------

def generate_dj_intros(
    cfg: dict,
    weather: dict,
    ctx: dict,
    show_block: str,
    songs: list[dict],
) -> list[dict]:
    dj = cfg["dj"]
    station = cfg["station"]
    song_list_text = "\n".join(
        f"  Song {s['id']}: genre={s.get('genre','')}, mood={s.get('mood','')}"
        for s in songs
    )
    template_vars = {
        "dj_name": dj["name"],
        "station_name": station["name"],
        "dj_persona": dj["persona"].strip(),
        "count": len(songs),
        "time_of_day": ctx["time_of_day"],
        "hour": ctx["hour"],
        "season": ctx["season"],
        "weather_description": weather["description"],
        "show_block": show_block,
        "song_list": song_list_text,
    }
    prompt = _render_template(PROMPTS_DIR / "dj_intro.txt", template_vars)
    raw = _call_claude(prompt)

    try:
        intros = json.loads(raw)
        if not isinstance(intros, list):
            raise ValueError("Expected JSON array")
        return intros
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("Failed to parse DJ intros JSON: %s\nRaw: %s", exc, raw[:500])
        return []


# ---------------------------------------------------------------------------
# Talk show scripts
# ---------------------------------------------------------------------------

SHOW_CONFIGS = {
    "meditation_mondays": {
        "show_name": "Meditation Mondays",
        "host_name": "Guide",
        "host_persona": "Soft, slow, spacious delivery — like a voice that has nowhere to be.",
        "show_format": "Guided meditation",
        "duration_minutes": 20,
        "target_words": 2600,
        "body_minutes": 18,
        "show_notes": (
            "Guide the listener through a simple body-scan or breath-awareness meditation. "
            "Avoid complex visualizations. Prefer concrete sensations: weight, warmth, breath. "
            "Do not use the word 'chakra', 'manifest', or 'universe'. Keep it secular and grounded."
        ),
    },
    "jazz_at_dusk": {
        "show_name": "Jazz at Dusk",
        "host_name": "{dj_name}",
        "host_persona": "{dj_persona}",
        "show_format": "Jazz appreciation segment with DJ narration",
        "duration_minutes": 5,
        "target_words": 650,
        "body_minutes": 3,
        "show_notes": (
            "A short spoken intro to the Jazz at Dusk block. Reflect on the feeling of dusk, "
            "the history of jazz as evening music, what makes this hour feel alive. "
            "Sound like a friend who loves jazz, not a professor. Keep it warm and brief."
        ),
    },
    "sunday_folk": {
        "show_name": "Sunday Morning Folk",
        "host_name": "{dj_name}",
        "host_persona": "{dj_persona}",
        "show_format": "Sunday morning folk block intro",
        "duration_minutes": 5,
        "target_words": 650,
        "body_minutes": 3,
        "show_notes": (
            "A short spoken intro to Sunday Morning Folk. "
            "Capture the particular quality of Sunday morning — unhurried, a little quiet, "
            "a pot of coffee, nowhere to be. Evoke rather than describe. "
            "Brief is better than comprehensive."
        ),
    },
    "general": {
        "show_name": "Blue Hour Radio",
        "host_name": "{dj_name}",
        "host_persona": "{dj_persona}",
        "show_format": "General relaxation segment",
        "duration_minutes": 8,
        "target_words": 1040,
        "body_minutes": 6,
        "show_notes": (
            "A gentle spoken interlude — reflection on the present moment, a brief story, "
            "or a meditation on something small and beautiful. "
            "Not a meditation script — more like a quiet essay read aloud. "
            "End with something the listener can carry with them."
        ),
    },
}

EPISODE_THEMES = {
    "meditation_mondays": [
        "returning to stillness after a busy week",
        "the breath as an anchor",
        "noticing what's already here",
        "letting go of the day's tension",
        "the weight of the body at rest",
    ],
    "jazz_at_dusk": [
        "why jazz lives in the evening",
        "the geography of a good chord",
        "what musicians know about time",
        "blue notes and blue skies",
        "improvisation as a way of being",
    ],
    "sunday_folk": [
        "songs that come from the land",
        "what it means to slow down on purpose",
        "the old songs that still know something",
        "front porch music and the art of nowhere to be",
        "voices that sound like they've been somewhere",
    ],
    "general": [
        "small beautiful things",
        "the quiet between sounds",
        "what staying still teaches you",
        "the particular quality of this hour",
        "gratitude without performance",
    ],
}


def generate_talkshow_script(
    cfg: dict,
    weather: dict,
    ctx: dict,
    show_block: str,
) -> str:
    import random

    show_key = show_block if show_block in SHOW_CONFIGS else "general"
    show_cfg = dict(SHOW_CONFIGS[show_key])

    dj = cfg["dj"]
    for key in ("host_name", "host_persona", "show_notes"):
        if isinstance(show_cfg.get(key), str):
            show_cfg[key] = (
                show_cfg[key]
                .replace("{dj_name}", dj["name"])
                .replace("{dj_persona}", dj["persona"].strip())
            )

    themes = EPISODE_THEMES.get(show_key, EPISODE_THEMES["general"])
    theme = random.choice(themes)

    template_vars = {
        **show_cfg,
        "station_name": cfg["station"]["name"],
        "date": ctx["date"],
        "season": ctx["season"],
        "time_of_day": ctx["time_of_day"],
        "weather_description": weather["description"],
        "episode_theme": theme,
    }
    prompt = _render_template(PROMPTS_DIR / "talkshow_script.txt", template_vars)
    return _call_claude(prompt, timeout=180)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    loc = cfg["location"]
    weather = fetch_weather(loc["latitude"], loc["longitude"])
    ctx = time_context()
    block = current_show_block()

    log.info("Time context: %s / %s / show_block=%s", ctx["time_of_day"], ctx["season"], block)
    log.info("Weather: %s, %s°C", weather["description"], weather["temp"])

    if "--music" in sys.argv:
        prompts = generate_music_prompts(cfg, weather, ctx, block, count=2)
        print(json.dumps(prompts, indent=2))

    elif "--dj" in sys.argv:
        dummy_songs = [
            {"id": 1, "genre": "jazz", "mood": "mellow"},
            {"id": 2, "genre": "folk", "mood": "warm"},
        ]
        intros = generate_dj_intros(cfg, weather, ctx, block, dummy_songs)
        print(json.dumps(intros, indent=2))

    elif "--show" in sys.argv:
        script = generate_talkshow_script(cfg, weather, ctx, block)
        print(script)

    else:
        print("Usage: python claude_scripts.py [--music | --dj | --show]")
