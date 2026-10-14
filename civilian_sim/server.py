"""Local-only stdlib HTTP server. Run from repository root as a module."""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse

from .audit import AuditLog
from .engine import Engine
from .integrations import copilot, weather_preview
from .model import timestamp

ROOT = Path(__file__).resolve().parent


def make_handler(engine, runtime):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log approval tokens or what-if request bodies.
            super().log_message(fmt, *args)

        def send(self, value, status=200, content_type="application/json; charset=utf-8", download=None):
            data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(data)

        def trusted(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in hosts:
                raise PermissionError("Untrusted Host header")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in hosts}:
                raise PermissionError("Cross-origin requests are not allowed")

        def do_GET(self):
            try:
                self.trusted()
                route = urlparse(self.path)
                if route.path == "/":
                    return self.send((ROOT / "static" / "index.html").read_bytes(), content_type="text/html; charset=utf-8")
                if route.path == "/api/state":
                    return self.send(engine.state())
                if route.path == "/api/audit/verify":
                    return self.send(engine.audit.verify())
                if route.path == "/api/frontier":
                    return self.send(engine.frontier())
                if route.path == "/api/benchmarks":
                    path = runtime / "benchmarks.json"
                    if not path.exists():
                        return self.send({"error": "No benchmark output. Run python -m civilian_sim.benchmark"}, 404)
                    return self.send(path.read_bytes())
                if route.path == "/api/benchmarks/scaling":
                    path = runtime / "benchmarks.svg"
                    if not path.exists():
                        return self.send({"error": "No scaling chart. Run python -m civilian_sim.benchmark"}, 404)
                    return self.send(path.read_bytes(), content_type="image/svg+xml; charset=utf-8")
                if route.path == "/api/export":
                    state = engine.state()
                    return self.send({"format": "civilian-relief-dispatch/1.0", "label": "SIMULATION ONLY — not an aviation clearance",
                                      "sim_time": state["sim_time"], "state_version": state["version"],
                                      "audit_head": state["audit"]["head"], "tasks": state["tasks"],
                                      "assignments": [{**a, "departure_utc": timestamp(a["start"]), "arrival_utc": timestamp(a["end"])} for a in state["plan"]["assignments"]],
                                      "active": state["active"], "unscheduled": state["plan"]["unscheduled"],
                                      "metrics": state["plan"]["metrics"]}, download="civilian-dispatch.json")
                if route.path == "/api/weather":
                    args = parse_qs(route.query)
                    return self.send(weather_preview(args["base"][0], args["latitude"][0], args["longitude"][0]))
                self.send({"error": "Not found"}, 404)
            except PermissionError as exc:
                self.send({"error": str(exc)}, 403)
            except (ValueError, KeyError, TypeError) as exc:
                self.send({"error": str(exc)}, 400)
            except (OSError, TimeoutError) as exc:
                self.send({"error": "Resource/integration unavailable: " + str(exc)}, 503)

        def do_POST(self):
            try:
                self.trusted()
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Content-Type must be application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("Request must be 1–16384 bytes")
                self.connection.settimeout(10)
                value = json.loads(self.rfile.read(length), parse_constant=lambda v: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
                if not isinstance(value, dict):
                    raise ValueError("Body must be an object")
                route = urlparse(self.path).path
                if route == "/api/control":
                    engine.control(value)
                elif route == "/api/reset":
                    engine.reset(value["seed"])
                elif route == "/api/preferences":
                    engine.preferences(value)
                elif route == "/api/whatif":
                    return self.send(engine.preview(value["scenario"]))
                elif route == "/api/copilot":
                    with engine.lock:
                        snapshot = deepcopy(engine.world)
                    scenario = copilot(value["text"], value.get("provider", "rules"), snapshot)
                    return self.send(engine.preview(scenario))
                elif route == "/api/approve":
                    engine.approve(value["proposal_id"], value["reviewer"], value["token"])
                else:
                    return self.send({"error": "Not found"}, 404)
                self.send(engine.state())
            except PermissionError as exc:
                self.send({"error": str(exc)}, 403)
            except (ValueError, KeyError, TypeError) as exc:
                self.send({"error": str(exc)}, 400)
            except (OSError, TimeoutError) as exc:
                self.send({"error": "Resource/integration unavailable: " + str(exc)}, 503)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    args = parser.parse_args()
    reviewers = {"reviewer-a": os.environ.get("CIVILIAN_REVIEWER_A", secrets.token_urlsafe(18)),
                 "reviewer-b": os.environ.get("CIVILIAN_REVIEWER_B", secrets.token_urlsafe(18))}
    engine = Engine(AuditLog(args.runtime / "audit.jsonl"), reviewers, args.seed)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine, args.runtime))
    stop = threading.Event()
    def clock():
        last = time.monotonic()
        while not stop.wait(.25):
            now = time.monotonic()
            try:
                engine.advance(now - last)
            except Exception as exc:
                # Fail closed rather than silently continuing unaudited dispatch.
                with engine.lock:
                    engine.paused = True
                print(f"Simulation paused after error: {exc}", flush=True)
            last = now
    ticker = threading.Thread(target=clock, daemon=True)
    ticker.start()
    print(f"Civilian relief simulator: http://127.0.0.1:{args.port}", flush=True)
    print("Demo reviewer credentials (keep separate; not production identity assurance):", flush=True)
    for name, token in reviewers.items():
        print(f"  {name}: {token}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        ticker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()