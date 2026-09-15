"""Build the real thing a test needs before it asserts anything about trust.

Every readiness fact, gate or verification in these helpers comes from material that actually
exists in the temp repository: a source file that really has that digest, a report that really
was written, an approval file that really contains the approver's decision. Nothing here hands
`sm.set_readiness` a pre-verified object, because that would test the fixture instead of the
entry point the acceptance criteria talk about.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evidence_provenance as ep  # noqa: E402
import execution_state_manager as sm  # noqa: E402

# Reports and approvals live under the evidence tree, which the repository fingerprint treats
# as generated: recording proof must never look like the code under test moved.
REPORTS_DIR = ".orchestrator/evidence/reports"
APPROVALS_REL = ".orchestrator/evidence/approvals.json"
DEFAULT_SOURCE = "src/app.py"
DEFAULT_REQUIREMENT = "requirements/story.md"

# Which kind of evidence each readiness key is reachable with, when a test does not care.
READINESS_KINDS = {
    "behavior_change": "mechanical_observation",
    "acceptance_criteria_present": "mechanical_observation",
    "implementation_tasks_complete": "mechanical_observation",
    "acceptance_satisfied": "mechanical_observation",
    "sdd_ready": "human_approval",
}


def write_source(repo: pathlib.Path, rel: str = DEFAULT_SOURCE,
                 content: str = "def answer():\n    return 42\n") -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rel


def write_requirement(repo: pathlib.Path, rel: str = DEFAULT_REQUIREMENT,
                      content: str = "# Story\n\nReturn 42.\n") -> str:
    return write_source(repo, rel, content)


def pick_source(repo: pathlib.Path, preferred: tuple[str, ...] = (DEFAULT_SOURCE,)) -> str:
    """Prefer a file that is already part of the analyzed content.

    A dependency that a test creates after analysis would silently invalidate the snapshot the
    test is about to trust, so existing tracked files are used whenever the repository has any.
    """
    for name in preferred:
        if (repo / name).is_file():
            return name
    candidates = [n for n in _tracked(repo)
                  if not n.startswith(".orchestrator") and (repo / n).is_file()
                  and len(n.encode("utf-8")) < 4096]
    if candidates:
        return sorted(candidates)[0]
    return write_source(repo)


def dependency(repo: pathlib.Path, object_kind: str, object_id: str) -> dict:
    """Declare a dependency whose revision is read from the object, exactly as the verifier does."""
    bare = {"object_kind": object_kind, "object_id": object_id}
    resolution = ep.resolve_dependency(repo, bare)
    if not resolution.get("resolved"):
        raise AssertionError(f"cannot resolve {object_kind} {object_id}: {resolution}")
    return {**bare, "revision": resolution["revision"]}


def code_dependency(repo: pathlib.Path, rel: str | None = None) -> dict:
    target = rel or pick_source(repo)
    if rel:
        write_source(repo, rel)
    return dependency(repo, "code_under_test", target)


def pick_requirement(repo: pathlib.Path, preferred: tuple[str, ...] = (DEFAULT_REQUIREMENT,)) -> str:
    for name in preferred:
        if (repo / name).is_file():
            return name
    for name in _tracked(repo):
        if name.endswith((".md", ".txt")) and not name.startswith(".orchestrator"):
            return name
    return write_requirement(repo)


def _tracked(repo: pathlib.Path) -> list[str]:
    """Files that already take part in the analyzed content, tracked or not.

    Both sources are asked: a requirement written after the last commit is still part of the
    project, and inventing a new file instead would change what the claim is about.
    """
    names: set[str] = set()
    try:
        raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo, stderr=subprocess.DEVNULL)
        names.update(os.fsdecode(x) for x in raw.split(b"\0") if x)
    except (OSError, subprocess.CalledProcessError):
        pass
    skip = {".git", ".orchestrator", "node_modules", "__pycache__", ".venv", ".claude", ".codex"}
    names.update(p.relative_to(repo).as_posix() for p in repo.rglob("*")
                 if p.is_file() and not set(p.relative_to(repo).parts) & skip)
    return sorted(names)


def requirement_dependency(repo: pathlib.Path, rel: str | None = None,
                           content: str | None = None) -> dict:
    target = rel or pick_requirement(repo)
    if content is not None or not (repo / target).is_file():
        write_requirement(repo, target, content or "# Story\n\nReturn 42.\n")
    return dependency(repo, "requirement_revision", target)


def native_dependency(repo: pathlib.Path, rel: str = "docs/sprint-status.yaml",
                      content: str | None = None) -> dict:
    if content is not None or not (repo / rel).exists():
        write_source(repo, rel, content or "development_status:\n  W-1: ready-for-dev\n")
    return dependency(repo, "native_source", rel)


def write_negative_proof(repo: pathlib.Path, fact_path: str, *,
                         query: str = "no contradicting requirement found",
                         matches: int = 0) -> str:
    """Record the search that found nothing. A negative fact needs that search, not a title."""
    rel = f".orchestrator/evidence/negative-proof/{fact_path.replace('.', '-')}.json"
    payload = {"fact": fact_path, "search": query, "matches": matches,
               "command": f"rg -n '{query}' --stats ."}
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rel


def negative_proof_entry(repo: pathlib.Path, fact_path: str, **kwargs) -> dict:
    """A negative proof object in the shape `fact_resolver` expects."""
    return {"ref": write_negative_proof(repo, fact_path, **kwargs),
            "search": kwargs.get("query", "no contradicting requirement found"),
            "matches": kwargs.get("matches", 0)}


def write_report(repo: pathlib.Path, name: str = "unit-tests.json", *, status: str = "passed",
                 exit_code: int = 0, command: str = "python3 -m unittest discover -s tests",
                 tests: dict | None = None) -> dict:
    """Write a report the way the verification entry point would produce one."""
    payload = {"status": status, "exit_code": exit_code, "command": command}
    if tests is not None:
        payload["tests"] = tests
    rel = f"{REPORTS_DIR}/{name}"
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": rel, "exit_code": exit_code,
            "digest": hashlib.sha256((repo / rel).read_bytes()).hexdigest()}


def write_approval(repo: pathlib.Path, *, approval_id: str = "appr-1", subject: str = "W-1",
                   decision: str = "approved", approver: str = "reviewer-1",
                   path: str = APPROVALS_REL) -> str:
    """Write an approval file: the human decision lives outside the claim that cites it."""
    target = repo / path
    doc: dict = {"approvals": []}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("approvals"), list):
                doc = loaded
        except (OSError, ValueError):
            doc = {"approvals": []}
    doc["approvals"].append({"id": approval_id, "subject": subject, "decision": decision,
                             "approver": approver})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_readiness_record(repo: pathlib.Path, key: str, *, work_item_id: str = "W-1",
                           requirement_revision: str | None = None,
                           kind: str | None = None, outcome: str | None = None,
                           producer: str = "test-runner",
                           extra_dependencies: list[dict] | None = None,
                           report_name: str | None = None, report_status: str = "passed",
                           exit_code: int = 0, subject: str | None = None) -> dict:
    """Build a claim that is verifiable because everything it cites exists right now."""
    kind = kind or READINESS_KINDS.get(key, "mechanical_observation")
    rule = ep.READINESS_SOURCE_RULES.get(key) or {}
    claim_type = str(rule.get("claim_type") or "readiness")
    depends_on: list[dict] = [requirement_dependency(repo)]
    if kind == "mechanical_observation":
        depends_on.append(code_dependency(repo))
    depends_on.extend(extra_dependencies or [])
    record = ep.build_record(
        kind=kind, claim_type=claim_type, work_item_id=work_item_id,
        requirement_revision=requirement_revision,
        outcome=outcome or ("passed" if report_status == "passed" else "failed"),
        producer=producer, source={"type": "path"},
        depends_on=depends_on,
    )
    if kind == "mechanical_observation":
        if report_name is None:
            report_name = f"{key}-report.json"
        report = write_report(repo, report_name, status=report_status, exit_code=exit_code)
        record["report"] = report
    if kind == "human_approval":
        approval_id = f"appr-{key}"
        write_approval(repo, approval_id=approval_id, subject=subject or work_item_id)
        record["approval"] = {"source": APPROVALS_REL, "subject": subject or work_item_id,
                              "revision": approval_id}
    return record


def establish_readiness(state_path: pathlib.Path, key: str, *, actor: str = "test",
                        repo: pathlib.Path | None = None, **kwargs) -> dict:
    """Set one readiness key true through real, currently verifiable evidence."""
    repo = (repo or sm.repository_snapshot.repo_from_state(state_path)).resolve()
    state = sm._load(state_path)
    work_item = state.get("work_item") or {}
    work_item_id = str(work_item.get("id") or "W-1")
    context = requirement_revision = kwargs.pop("requirement_revision", None)
    if context is None:
        context = work_item.get("requirement_revision")
    record = build_readiness_record(repo, key, work_item_id=work_item_id,
                                    requirement_revision=context, **kwargs)
    sm.set_readiness(state_path, key, True, actor, record.get("report", {}).get("path") or key,
                     state["revision"], evidence_record=record)
    return sm._load(state_path).get("readiness_evidence", {}).get(key, {})


def progress_record(repo: pathlib.Path, *, completed: int = 1, total: int = 1,
                    work_item_id: str = "W-1", requirement_revision: str | None = None) -> dict:
    """A real observation that tasks were finished: a report that says so, plus its dependencies."""
    report = write_report(repo, f"tasks-{completed}.json", command=f"python3 -m pytest tasks -k {completed}")
    return ep.build_record(
        kind="mechanical_observation", claim_type="task_progress", work_item_id=work_item_id,
        outcome="passed", producer="pytest", requirement_revision=requirement_revision,
        depends_on=[code_dependency(repo), requirement_dependency(repo)], report=report,
        source={"type": "test_report", "ref": report["path"]},
        extra={"completed": completed, "total": total},
    )


def establish_progress(state_path: pathlib.Path, completed: int = 1, total: int | None = None, *,
                       actor: str = "test", repo: pathlib.Path | None = None,
                       **kwargs) -> dict:
    """Advance progress through real, currently verifiable evidence; returns the stored evidence id."""
    repo = (repo or sm.repository_snapshot.repo_from_state(state_path)).resolve()
    state = sm._load(state_path)
    work_item = state.get("work_item") or {}
    work_item_id = str(work_item.get("id") or "W-1")
    total = total if total is not None else completed
    record = progress_record(repo, completed=completed, total=total, work_item_id=work_item_id,
                             requirement_revision=kwargs.pop("requirement_revision", None)
                             or work_item.get("requirement_revision"), **kwargs)
    ep.store_evidence(repo, record, checked_at=sm.utc_now())
    evidence_id = ep.evidence_id(record)
    state = sm.set_progress(state_path, completed, total, actor, evidence_id, state["revision"],
                            evidence_record=record)
    return {"evidence_id": evidence_id, "state": state}


def establish(state_path: pathlib.Path, *keys: str, actor: str = "test",
              repo: pathlib.Path | None = None, **kwargs) -> None:
    for key in keys:
        establish_readiness(state_path, key, actor=actor, repo=repo, **kwargs)


def passed_gate_record(repo: pathlib.Path, name: str = "unit-tests", *,
                       work_item_id: str = "W-1", requirement_revision: str | None = None,
                       exit_code: int = 0, status: str = "passed") -> tuple[dict, dict]:
    """A gate may only record passed together with a report that really says passed."""
    report = write_report(repo, f"{name}.json", status=status, exit_code=exit_code,
                          command=f"python3 -m pytest {name}")
    record = ep.build_record(
        kind="mechanical_observation", claim_type="gate_result", work_item_id=work_item_id,
        requirement_revision=requirement_revision,
        outcome="passed" if status == "passed" else "failed",
        producer="test-runner", source={"type": "path"},
        depends_on=[requirement_dependency(repo), code_dependency(repo)],
        report=report,
    )
    return record, report
