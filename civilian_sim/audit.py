"""Hash-chained JSONL audit; tamper evidence, not tamper prevention."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from threading import RLock


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


class AuditLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        result = self.verify()
        if not result["valid"]:
            raise ValueError("Audit integrity failure: " + result["error"])

    def verify(self):
        with self.lock:
            previous, count = "0" * 64, 0
            try:
                if self.path.exists():
                    with self.path.open(encoding="utf-8") as stream:
                        for line in stream:
                            record = json.loads(line)
                            actual = record.pop("hash")
                            if record["previous_hash"] != previous or record["sequence"] != count + 1:
                                raise ValueError(f"Broken chain at entry {count+1}")
                            if digest(record) != actual:
                                raise ValueError(f"Modified entry {count+1}")
                            previous, count = actual, count + 1
                return {"valid": True, "entries": count, "head": previous, "error": None}
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                return {"valid": False, "entries": count, "head": previous, "error": str(exc)}

    def append(self, kind, payload):
        with self.lock:
            status = self.verify()
            if not status["valid"]:
                raise ValueError("Refusing to extend a damaged audit log")
            record = {"sequence": status["entries"] + 1, "previous_hash": status["head"],
                      "timestamp": datetime.now(timezone.utc).isoformat(),
                      "kind": kind, "payload": payload}
            record["hash"] = digest(record)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
                stream.flush()
            return record["hash"]