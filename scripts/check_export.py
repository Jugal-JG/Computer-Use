"""Verify artifact/evidence consistency and absence of the configured API key."""

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from visual_agent.contracts import Artifact


def artifact_hash(path):
    raw = json.loads(path.read_text())
    if raw.get("schema_version") == "1.0":
        # Integrity-check archived bytes; never implicitly upgrade or replay v1.
        return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return Artifact.model_validate(raw).digest()


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    bad = []
    checked = 0
    for directory in ["src", "tests", "scripts", "config", "schemas", "evidence"]:
        for path in (root / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            checked += 1
            if key and key.encode() in path.read_bytes():
                bad.append(path.relative_to(root).as_posix())
    for name in ["README.md", "REPORT.md", ".env.example", "pyproject.toml", "requirements.lock"]:
        checked += 1
        if key and key.encode() in (root / name).read_bytes():
            bad.append(name)
    recorded_hash = artifact_hash(root / "evidence/capability/statement-request-v1.json")
    for result in (root / "evidence/replay").glob("*/result.json"):
        row = json.loads(result.read_text())
        assert row["artifact_hash"] == recorded_hash
        assert row["llm_calls"] == 0
    human_hash = artifact_hash(root / "evidence/human_handoff/artifact.json")
    assert (
        json.loads((root / "evidence/human_handoff/result.json").read_text())["artifact_hash"]
        == human_hash
    )
    dynamic = root / "evidence/dynamic-v3"
    if dynamic.exists():
        dynamic_hash = artifact_hash(dynamic / "capability.json")
        for phase in ("discovery", "replay"):
            result = json.loads((dynamic / phase / "result.json").read_text())
            assert result["artifact_hash"] == dynamic_hash and result["status"] == "success"
            assert (result["llm_calls"] == 0) == (phase == "replay")
    print(
        json.dumps(
            {
                "files_checked": checked,
                "configured_key_found_in_export": bool(bad),
                "affected_paths": bad,
                "artifact_hashes_consistent": True,
            }
        )
    )
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
