"""Copy only reviewed evidence products; never export .env, SQLite, or debug captures."""

import hashlib
import json
import shutil
from pathlib import Path

from visual_agent.contracts import Artifact

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "evidence"


def copy_run(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in ["events.jsonl", "final.png", "intervention.png"]:
        if (source / name).exists():
            shutil.copy2(source / name, destination / name)
    data = json.loads((source / "result.json").read_text())
    data["evidence_dir"] = destination.relative_to(ROOT).as_posix()
    (destination / "result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    DEST.mkdir(exist_ok=True)
    capability = ROOT / ".runs/discovered-35lite-v1.json"
    artifact = Artifact.model_validate_json(capability.read_text())
    assert artifact.provenance["genuine_discovery"] is True
    (DEST / "capability").mkdir(exist_ok=True)
    shutil.copy2(capability, DEST / "capability/statement-request-v1.json")
    copy_run(ROOT / ".runs/discovery-compiled", DEST / "discovery")
    for scenario in [
        "normal",
        "not_found",
        "validation",
        "permission",
        "slow",
        "notice",
        "app_error",
    ]:
        copy_run(ROOT / ".runs/verified-suite" / scenario, DEST / "replay" / scenario)
    copy_run(ROOT / ".runs/manual-handoff", DEST / "human_handoff")
    shutil.copy2(ROOT / ".runs/authored-fixture.json", DEST / "human_handoff/artifact.json")
    # Explicit labels prevent simulated or authored evidence being mistaken for discovery.
    manifest = {
        "discovery_model": artifact.provenance["model"],
        "discovered_artifact_hash": artifact.digest(),
        "discovery": "genuine_llm_run",
        "replay": "same_discovered_artifact_new_inputs_zero_llm",
        "human_handoff": "actual_user_operated_panel_with_authored_fixture",
        "files": {},
    }
    for path in sorted(DEST.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][path.relative_to(DEST).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Exported curated synthetic evidence; no credentials or raw debug screenshots copied.")


if __name__ == "__main__":
    main()
