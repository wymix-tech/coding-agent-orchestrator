"""Shared helpers for CAO tests."""
import json
import pathlib


def verify_via_config(repo, run, command="python3 -m unittest --help"):
    """Configure the verification standard in config, then run `cao verify`.

    `cao done` only accepts a config-sourced verification, so tests that walk a
    Work Item to completion must put the standard in config.json rather than
    passing `verify --script`. `configured_at` is backdated before the Work
    Item's start, mirroring a standard fixed during provisioning.
    """
    cfg_path = pathlib.Path(repo) / ".orchestrator" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("verification", {})["scripts"] = [
        {"name": "script-1", "command": command, "required": True}
    ]
    work_files = list((pathlib.Path(repo) / ".orchestrator" / "work").glob("*.json"))
    started = json.loads(work_files[0].read_text(encoding="utf-8"))["started_at"] if work_files else 0
    cfg["verification"]["configured_at"] = started - 100
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return run(repo, "verify")
