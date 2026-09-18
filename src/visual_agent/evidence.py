import io
import json
import re
import sqlite3
import time
from pathlib import Path

from PIL import Image


def scrub(value):
    if isinstance(value, str):
        value = re.sub(r"\b\d{5,}\b", "[REDACTED_ID]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
        return value[:500]
    if isinstance(value, dict):
        return {
            k: scrub(v)
            for k, v in value.items()
            if k not in {"api_key", "token", "text_input", "prompt", "response"}
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


class Evidence:
    def __init__(self, directory, run_id):
        self.directory = Path(directory)
        if (self.directory / "events.jsonl").exists():
            self.directory = self.directory / run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.db = sqlite3.connect(self.directory / "state.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS checkpoint (run_id TEXT PRIMARY KEY, state TEXT, step TEXT, updated REAL)"
        )

    def event(self, event, **fields):
        row = scrub({"time": time.time(), "run_id": self.run_id, "event": event, **fields})
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def checkpoint(self, state, step):
        self.db.execute(
            "INSERT OR REPLACE INTO checkpoint VALUES (?,?,?,?)",
            (self.run_id, state, step, time.time()),
        )
        self.db.commit()

    def screenshot(self, png, name, view=None):
        # Persist only known static UI pixels. Everything else is masked, including unknown text.
        source = Image.open(io.BytesIO(png)).convert("RGB")
        redacted = Image.new("RGB", source.size, "#243541")
        safe = [
            "Member Search",
            "Member Details",
            "Savings Account",
            "Statement Request",
            "Statement Review",
            "Member not found",
            "Permission denied",
            "Application unavailable",
            "Validation rejected",
            "Service notice",
            "Session expired",
            "Supervisor acknowledgement",
            "Search",
            "Open savings",
            "Prepare statement",
            "Review request",
            "Dismiss notice",
            "Loading",
        ]
        if view:
            for text in safe:
                for b in view.find(text):
                    box = (b.x, b.y, b.x + b.w, b.y + b.h)
                    redacted.paste(source.crop(box), (b.x, b.y))
        redacted.save(self.directory / name)

    def result(self, result):
        (self.directory / "result.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        self.checkpoint("TERMINAL", result.step)

    def close(self):
        self.db.close()
