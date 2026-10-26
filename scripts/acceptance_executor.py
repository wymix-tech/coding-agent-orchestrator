"""Copy into a project and declare real, criterion-specific command checks.

Usage: python acceptance_executor.py --checks acceptance-checks.json
The request environment variable contains JSON, never a request filename.
Missing checks block acceptance; this template never manufactures a pass.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def local_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Expected a repository-relative file")
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("File escapes the project")
    return path


def execute(criterion, check, root, *, request=None):
    fallback = [{"path": criterion["source"], "reason": "No executable check is configured for this criterion"}]
    if not isinstance(check, dict):
        return {"id": criterion["id"], "status": "blocked", "evidence": fallback}, 2
    try:
        command, evidence = check.get("command"), check.get("evidence")
        strict = (request or {}).get('declaration', {}).get('evidence_policy') == 'criterion-checks-v1'
        if strict and any(not isinstance(check.get(key), str) or not check[key].strip()
                          for key in ('selector', 'assertion')):
            raise ValueError('Each criterion needs a selector and assertion identifying its actual check')
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("check.command must be a nonempty argv array")
        if not isinstance(evidence, list) or not evidence or not all(local_file(root, value).is_file() for value in evidence):
            raise ValueError("check.evidence must name existing project files")
        timeout = check.get("timeout_seconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise ValueError("check.timeout_seconds must be in (0, 3600]")
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=timeout)
        details = (result.stdout + result.stderr)[-3000:]
        if details:
            print(details, file=sys.stderr)
        status = {0: "passed", 1: "failed", 2: "blocked"}.get(result.returncode, "blocked")
        code = result.returncode if result.returncode in {0, 1, 2} else 3
        reason = f"Executed {shlex.join(command)}; exit_code={result.returncode}. {details}"
        return {"id": criterion["id"], "status": status,
                "evidence": [{"path": value, "reason": reason} for value in evidence],
                **({'check': {'criterion_id': criterion['id'], 'run_id': request['run_id'],
                             'selector': check['selector'], 'assertion': check['assertion'],
                             'observed': reason}} if strict else {})}, code
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        fallback[0]["reason"] = str(exc)
        return {"id": criterion["id"], "status": "blocked", "evidence": fallback}, 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checks", help="Project JSON mapping each criterion ID to command/evidence")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        request = json.loads(os.environ["CAO_SDD_ACCEPTANCE_REQUEST"])
        criteria = request["declaration"]["criteria"]
        if not isinstance(criteria, list) or not criteria:
            raise ValueError("request.declaration.criteria must be a nonempty array")
        checks = json.loads(local_file(root, args.checks).read_text()) if args.checks else {}
        if not isinstance(checks, dict):
            raise ValueError("checks must be an object keyed by criterion ID")
        items, codes = [], []
        for criterion in criteria:
            item, code = execute(criterion, checks.get(criterion["id"]), root, request=request)
            items.append(item)
            codes.append(code)
        report = {**{key: request[key] for key in ("run_id", "input_sha256", "provider")}, "items": items}
        for relative in request["declaration"].get("outputs", []):
            output = local_file(root, relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 3 if 3 in codes else 1 if 1 in codes else 2 if 2 in codes else 0
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"Invalid acceptance input: {exc}. CAO_SDD_ACCEPTANCE_REQUEST must contain a JSON request; see cao sdd acceptance protocol.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
