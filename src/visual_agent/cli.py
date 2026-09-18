import argparse
import asyncio
import json
import os
import socket
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from .capability import discovery_draft, fixture_artifact
from .contracts import Artifact, Inputs
from .policy import Policy


def load_artifact(path):
    artifact_path = Path(path)
    if not artifact_path.is_file():
        raise FileNotFoundError(f"ARTIFACT_NOT_FOUND: {artifact_path}")
    raw = artifact_path.read_text(encoding="utf-8")
    try:
        version = json.loads(raw).get("schema_version")
    except (json.JSONDecodeError, AttributeError):
        version = None
    if version == "1.0":
        raise SystemExit(
            "Schema-v1 artifacts are not replayed implicitly; run "
            f"'visual-agent migrate-v1 {path} NEW.json'."
        )
    return Artifact.model_validate_json(raw)


async def execute(args):
    import uvicorn

    from .engine import Run
    from .operator import create_operator
    from .surface import BrowserSurface

    inputs = Inputs.model_validate_json(args.inputs)
    artifact = load_artifact(args.artifact) if args.command == "replay" else discovery_draft()
    policy = (
        Policy.model_validate_json(Path(args.policy).read_text())
        if args.policy
        else Policy(origin=args.target)
    )
    if args.target != policy.origin:
        raise ValueError("Target must match policy origin")
    surface = BrowserSurface(
        policy,
        headed=args.headed,
        viewport=(args.viewport_width, args.viewport_height),
        device_scale_factor=args.device_scale_factor,
        window_following=args.window_following,
    )
    directory = args.evidence or str(Path(".runs") / f"{args.command}-{uuid.uuid4().hex[:10]}")
    run = None
    server = None
    server_task = None
    try:
        await surface.start()
        run = Run(
            surface,
            artifact,
            inputs,
            directory,
            args.intervention_timeout,
            step_delay_ms=args.step_delay_ms,
        )
        app, token = create_operator(run)
        sock = socket.socket()
        sock.bind(("127.0.0.1", args.operator_port))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        )
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        run.operator_url = f"http://127.0.0.1:{port}/#{token}"
        print(f"Operator panel: {run.operator_url}", flush=True)
        if args.command == "discover":
            from .discovery import discover

            result = await discover(run, args.goal, args.output)
        else:
            result = await run.replay()
        print(result.model_dump_json(indent=2), flush=True)
        if args.keep_open:
            await asyncio.to_thread(input, "Run finished. Press Enter to close the browser: ")
        return 0 if result.status in {"success", "business_outcome"} else 1
    finally:
        if server:
            server.should_exit = True
        if server_task:
            await server_task
        await surface.close()
        if run:
            run.evidence.close()


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="Visual-only local computer-use capabilities")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    suite_parser = sub.add_parser("demo-suite")
    suite_parser.add_argument("--artifact", required=True)
    suite_parser.add_argument("--evidence", default=".runs/demo-suite")
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--output", default=".runs/authored-fixture.json")
    schema = sub.add_parser("schema")
    schema.add_argument("--output", default="schemas/capability.schema.json")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("artifact")
    migrate = sub.add_parser("migrate-v1")
    migrate.add_argument("input")
    migrate.add_argument("output")
    for name in ["discover", "replay"]:
        q = sub.add_parser(name)
        q.add_argument("--target", default="http://127.0.0.1:8765")
        q.add_argument("--policy")
        q.add_argument("--inputs", required=True)
        q.add_argument("--headed", action="store_true")
        q.add_argument(
            "--keep-open",
            action="store_true",
            help="Keep a headed browser visible after the run until Enter is pressed",
        )
        q.add_argument(
            "--step-delay-ms",
            type=int,
            default=0,
            help="Optional delay before each step for watching or resizing the browser",
        )
        q.add_argument(
            "--window-following",
            action="store_true",
            help="Let the headed page resize with its browser window",
        )
        q.add_argument("--viewport-width", type=int, default=1280)
        q.add_argument("--viewport-height", type=int, default=800)
        q.add_argument("--device-scale-factor", type=float, default=1.0)
        q.add_argument("--evidence")
        q.add_argument("--operator-port", type=int, default=8766)
        q.add_argument("--intervention-timeout", type=int, default=300)
        if name == "replay":
            q.add_argument("--artifact", required=True)
        else:
            q.add_argument("--goal", required=True)
            q.add_argument("--output", required=True)
    a = p.parse_args()
    if a.command in {"discover", "replay"}:
        if a.keep_open and not a.headed:
            p.error("--keep-open requires --headed")
        if not 0 <= a.step_delay_ms <= 10000:
            p.error("--step-delay-ms must be between 0 and 10000")
    if a.command == "migrate-v1":
        source = json.loads(Path(a.input).read_text(encoding="utf-8"))
        if source.get("schema_version") != "1.0":
            raise SystemExit("Input is not a schema-v1 artifact.")
        output = Path(a.output)
        if output.exists():
            raise SystemExit("Output exists; choose a new filename.")
        width, height = source.pop("viewport", [1280, 800])
        source["schema_version"] = "2.0"
        source["capability_version"] = "2.0.0"
        source["discovery_viewport"] = [width, height]
        for name, target in source.get("targets", {}).items():
            x1, y1, x2, y2 = target.get("region", [0, 0, width, height])
            target["region"] = [x1 / width, y1 / height, x2 / width, y2 / height]
            target["relationships"] = (
                ["right_of", "same_row", "below"] if target.get("kind") == "field" else []
            )
            if name == "search":
                target["anchor_text"] = "Member ID"
                target["relationships"] = ["below"]
        artifact = Artifact.model_validate(source)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        print(output)
        return
    if a.command == "demo-suite":
        from .demo import suite

        raise SystemExit(asyncio.run(suite(a.artifact, a.evidence)))
    if a.command == "doctor":
        from playwright.sync_api import sync_playwright

        from .vision import pytesseract

        with sync_playwright() as pw:
            browser = Path(pw.chromium.executable_path)
            installed = browser.exists()
            if installed:
                probe = pw.chromium.launch()
                browser_version = probe.version
                probe.close()
            else:
                browser_version = None
        print(
            json.dumps(
                {
                    "python": sys.version.split()[0],
                    "tesseract": str(pytesseract.get_tesseract_version()),
                    "ocr_languages": pytesseract.get_languages(),
                    "chromium_installed": installed,
                    "chromium_version": browser_version,
                    "model_key_configured": bool(
                        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                    ),
                },
                indent=2,
            )
        )
        return
    if a.command in {"fixture", "schema"}:
        path = Path(a.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise SystemExit("Output exists; choose a new filename.")
        path.write_text(
            fixture_artifact().model_dump_json(indent=2)
            if a.command == "fixture"
            else json.dumps(Artifact.model_json_schema(), indent=2),
            encoding="utf-8",
        )
        print(path)
        return
    if a.command == "inspect":
        artifact = load_artifact(a.artifact)
        print(artifact.description)
        print("SHA256:", artifact.digest())
        print("Provenance:", artifact.provenance)
        for step in artifact.steps:
            print(f"{step.id}: {step.action} {step.target} -> {step.post.text}")
        return
    try:
        result = asyncio.run(execute(a))
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "status": "failure",
                    "code": "INVALID_CONFIGURATION",
                    "issues": [
                        {"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()
                    ],
                }
            )
        )
        result = 1
    except FileNotFoundError:
        print(
            json.dumps(
                {
                    "status": "failure",
                    "code": "ARTIFACT_NOT_FOUND",
                    "hint": "Use the JSON file created by discover after --output, not an evidence folder.",
                }
            )
        )
        result = 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failure",
                    "code": "STARTUP_OR_RUNTIME_ERROR",
                    "error_type": type(exc).__name__,
                    "hint": "Check that the proxy is running, Chromium is installed, and the operator port is free.",
                }
            )
        )
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
