"""Optional integrations: no network access on the default offline path."""
import json
import math
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .model import BASE_NAMES, validate_scenario

SCHEMA = {"oneOf": [
    {"type": "object", "additionalProperties": False,
     "required": ["type", "aircraft_id", "minutes"],
     "properties": {"type": {"const": "aircraft_delay"}, "aircraft_id": {"type": "string"}, "minutes": {"type": "integer", "minimum": 15, "maximum": 240}}},
    {"type": "object", "additionalProperties": False,
     "required": ["type", "base", "minutes"],
     "properties": {"type": {"const": "base_closure"}, "base": {"enum": list(BASE_NAMES)}, "minutes": {"type": "integer", "minimum": 15, "maximum": 240}}},
    {"type": "object", "additionalProperties": False,
     "required": ["type", "base", "priority", "duration"],
     "properties": {"type": {"const": "relief_request"}, "base": {"enum": list(BASE_NAMES)}, "priority": {"type": "integer", "minimum": 1, "maximum": 3}, "duration": {"type": "integer", "minimum": 30, "maximum": 120}}}
]}


def get_json(url, payload=None, timeout=10):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, headers={"Content-Type": "application/json", "User-Agent": "CivilianReliefDemo/1.0"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError("Integration response too large")
        return json.loads(data)


def parse_rules(text):
    match = re.fullmatch(r"delay (CV-\d{3}) (\d+) minutes", text, re.I)
    if match:
        return {"type": "aircraft_delay", "aircraft_id": match[1].upper(), "minutes": int(match[2])}
    match = re.fullmatch(r"close (Harbor|Meadow|Ridge) (\d+) minutes", text, re.I)
    if match:
        return {"type": "base_closure", "base": match[1].title(), "minutes": int(match[2])}
    match = re.fullmatch(r"add relief (Harbor|Meadow|Ridge) priority ([123]) duration (\d+) minutes", text, re.I)
    if match:
        return {"type": "relief_request", "base": match[1].title(), "priority": int(match[2]), "duration": int(match[3])}
    raise ValueError("Use 'delay CV-001 60 minutes', 'close Harbor 30 minutes', or 'add relief Harbor priority 1 duration 45 minutes'. Ollama enables flexible phrasing.")


def copilot(text, provider, world):
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("Enter 1–2000 characters")
    if provider == "rules":
        scenario = parse_rules(text.strip())
    elif provider == "ollama":
        # Endpoint is fixed loopback, never provided by untrusted user text.
        response = get_json("http://127.0.0.1:11434/api/chat", {
            "model": os.environ.get("CIVILIAN_OLLAMA_MODEL", "qwen2.5:3b"),
            "stream": False, "format": SCHEMA, "options": {"temperature": 0, "num_ctx": 2048},
            "messages": [{"role": "system", "content": "Translate a civilian disaster-relief what-if into exactly one JSON scenario matching the schema. You never assign aircraft or approve changes. Fictional bases: Harbor, Meadow, Ridge. Aircraft identifiers: " + ", ".join(a["id"] for a in world["aircraft"])},
                         {"role": "user", "content": text}]}, timeout=90)
        scenario = json.loads(response["message"]["content"])
    else:
        raise ValueError("Provider must be rules or ollama; cloud fallback is not enabled")
    return validate_scenario(scenario, world)


def weather_preview(base, latitude, longitude):
    if base not in BASE_NAMES:
        raise ValueError("Unknown fictional base")
    latitude, longitude = float(latitude), float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Invalid coordinates")
    query = urlencode({"latitude": latitude, "longitude": longitude,
                       "current": "temperature_2m,wind_speed_10m,weather_code",
                       "hourly": "visibility,wind_speed_10m,precipitation_probability",
                       "forecast_days": 1, "timezone": "UTC"})
    data = get_json("https://api.open-meteo.com/v1/forecast?" + query)
    return {"base": base, "mapping": {"latitude": latitude, "longitude": longitude},
            "source": "Open-Meteo (https://open-meteo.com/), CC BY 4.0", "data": data,
            "note": "Current real-world forecast preview only. Simulation is dated 2026-10-14; observations are not silently injected or used as flight-safety judgments."}