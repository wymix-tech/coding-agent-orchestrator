"""Build the real thing a test needs before it asserts anything about trust.

Material here is produced the way the project produces it, not declared the way a caller would
like it to look:

  * a report is the receipt of a command that really ran -- an argv and a return code the
    verifier did not invent, plus the code and requirement revisions in force while it ran;
  * an approval is recorded through the approval entry point, into the one store the project
    declares, and it names the artifact of the channel the human decision came through;
  * a dependency always names an object that exists in the temp repository.

A caller-shaped forgery is still possible, and other helpers here build one on purpose: those
are what the "same shape, not a source" tests reject. Nothing hands `sm.set_readiness` a
pre-verified object, because that would test the fixture instead of the entry point the
acceptance criteria talk about.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shlex
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
# Where the host records a human decision. The verifier re-reads it, so it stands for the
# channel the approval came through; in a real deployment the host writes it where the agent
# cannot.
APPROVAL_EVENTS_REL = ".orchestrator/evidence/approval-events.json"
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


def _state_scope(repo: pathlib.Path) -> dict:
    """The work item and requirement revision this project is on, read from its own state.

    A result is produced for the work item it will be spent on, against the requirement
    revision in force. Where the project has a state file, that is where the answer is; a
    receipt naming some other work item binds nothing here.
    """
    try:
        state = sm._load(repo / ".orchestrator" / "execution-state.yaml")
    except Exception:
        return {}
    work_item = state.get("work_item") or {}
    return {"work_item_id": work_item.get("id") or state.get("work_item_id"),
            "requirement_revision": work_item.get("requirement_revision"),
            "source_ref":work_item.get("requirement_source_ref"), "members":work_item.get("members")}


def work_item_id_for(repo: pathlib.Path, default: str = "W-1") -> str:
    return str(_state_scope(repo).get("work_item_id") or default)


def write_source(repo: pathlib.Path, rel: str = DEFAULT_SOURCE,
                 content: str = "def answer():\n    return 42\n") -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rel


def write_requirement(repo: pathlib.Path, rel: str = DEFAULT_REQUIREMENT,
                      content: str = "# Story\n\nReturn 42.\n") -> str:
    return write_source(repo, rel, content)


def pick_source(repo: pathlib.Path, preferred: tuple[str, ...] = (DEFAULT_SOURCE,),
                *, exclude: str | None = None) -> str:
    """Prefer a file that is already part of the analyzed content.

    A dependency that a test creates after analysis would silently invalidate the snapshot the
    test is about to trust, so existing tracked files are used whenever the repository has any.
    `exclude` keeps the object under test from being the requirement document: a claim that
    verifies the code against the requirement must name two different objects.
    """
    for name in preferred:
        if (repo / name).is_file() and name != exclude:
            return name
    candidates = [n for n in _tracked(repo)
                  if not n.startswith(".orchestrator") and (repo / n).is_file()
                  and len(n.encode("utf-8")) < 4096 and n != exclude
                  and not n.endswith((".md", ".txt"))]
    if candidates:
        return sorted(candidates)[0]
    target = write_source(repo)
    if target == exclude:
        target = write_source(repo, "src/module.py")
    return target


def dependency(repo: pathlib.Path, object_kind: str, object_id: str) -> dict:
    """Declare a dependency whose revision is read from the object, exactly as the verifier does."""
    bare = {"object_kind": object_kind, "object_id": object_id}
    resolution = ep.resolve_dependency(repo, bare)
    if not resolution.get("resolved"):
        raise AssertionError(f"cannot resolve {object_kind} {object_id}: {resolution}")
    return {**bare, "revision": resolution["revision"]}


def code_dependency(repo: pathlib.Path, rel: str | None = None, *,
                    exclude: str | None = None) -> dict:
    target = rel or pick_source(repo, exclude=exclude)
    if rel:
        write_source(repo, rel)
    if target == exclude:
        target = write_source(repo, "src/module.py")
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
                         matches: int = 0, scope: list[str] | None = None) -> str:
    """Record the search that found nothing. A negative fact needs that search, not a title."""
    rel = f".orchestrator/evidence/negative-proof/{fact_path.replace('.', '-')}.json"
    payload = {"fact": fact_path, "search": query, "matches": matches, "scope": scope or [],
               "command": f"rg -n '{query}' --stats ."}
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rel


def search_scope(repo: pathlib.Path) -> str:
    """The content a negative proof really searched: the analyzed content, not the proof file.

    Searching the proof document itself would find the query inside its own description and
    "prove" nothing was found while the record is literally full of it. The scope is the
    requirement this project actually has: inventing a second document beside the real one
    would search a file that never described the requirement.
    """
    return pick_requirement(repo)


def negative_proof_entry(repo: pathlib.Path, fact_path: str, **kwargs) -> dict:
    """A negative proof the resolver can re-run: what was searched, where, and the count."""
    query = kwargs.get("query", "no contradicting requirement found")
    scope = kwargs.get("scope") or [search_scope(repo)]
    return {"ref": write_negative_proof(repo, fact_path, query=query,
                                        matches=kwargs.get("matches", 0), scope=scope),
            "paths": scope,
            "search": query,
            "matches": kwargs.get("matches", 0)}


def publish_evidence_policy(repo: pathlib.Path, *, approval_authorities: tuple[str, ...] = ("reviewer-1",),
                            required_objects: list[str] | None = None) -> str:
    """Publish the trust configuration the project actually needs for human approvals.

    A name that is merely different from the producer is not an authority. Approvals only
    count as trust against a published list of who may approve what, so a test that needs a
    verified approval publishes that list instead of expecting the verifier to guess.
    """
    import yaml
    config = repo / ".orchestrator/config.yaml"
    doc: dict = {}
    if config.exists():
        try:
            loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = loaded
        except (OSError, yaml.YAMLError):
            doc = {}
    orch = doc.setdefault("orchestrator", {}) if isinstance(doc.get("orchestrator"), dict) else {}
    doc["orchestrator"] = orch
    evidence = orch.get("evidence") if isinstance(orch.get("evidence"), dict) else {}
    evidence["approval_authorities"] = list(approval_authorities)
    if required_objects is not None:
        evidence["required_objects"] = required_objects
    orch["evidence"] = evidence
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(doc, sort_keys=True, allow_unicode=True), encoding="utf-8")
    from trusted_evidence_fixture import publish_key
    for authority in approval_authorities:
        publish_key(repo, authority)
    return ".orchestrator/config.yaml"


def run_command(repo: pathlib.Path, *, name: str = "unit-tests.json", status: str = "passed",
                exit_code: int = 0, target: str = "gate:unit", work_item_id: str | None = None,
                requirement_revision: str | None = None, fact_path: str | None = None,
                value: bool | str | int | None = None, tests: dict | None = None,
                argv: list[str] | None = None, payload: dict | None = None) -> dict:
    """Run a command for real and write the receipt it produced where a report would go.

    The process really starts, really prints its payload and really exits with `exit_code`, so
    the receipt records an argv that was executed and a return code the verifier did not
    invent, together with the code and requirement revisions in force while it ran. A file that
    only says `status: passed` is not produced here.

    What the run is *for* is read from the project, not assumed: a receipt for a work item
    that is not this one -- or against a requirement revision that is not the one in force --
    is a receipt for something else.
    """
    scope = _state_scope(repo)
    pick_source(repo)  # All material must exist before the execution is observed.
    body = dict(payload) if payload is not None else {"status": status, "exit_code": exit_code}
    if fact_path is not None:
        body["fact_path"] = fact_path
        body["value"] = value
    if tests is not None:
        body['tests'] = tests
    if fact_path is not None and argv is None:
        from trusted_evidence_fixture import observer
        refs = [pick_requirement(repo)]
        if scope.get("source_ref"):
            refs.append(scope["source_ref"])
        refs.extend(scope.get("members") or [])
        argv = observer(repo, fact_path, value, sorted(set(refs)))
    if argv is None:
        argv = [sys.executable, "-c",
                "import json,sys;print(json.dumps(%r));sys.exit(%d)" % (body, int(exit_code))]
    if work_item_id is None:
        work_item_id = str(scope.get("work_item_id") or "W-1")
    if requirement_revision is None:
        # The revision in force while the command runs: the one the project is on, or the one
        # read from the requirement object itself. A receipt that does not say which
        # requirement it ran against is not a result about that requirement, and the consumer
        # may not fill it in afterwards.
        requirement_revision = (scope.get("requirement_revision")
                                or requirement_dependency(repo)["revision"])
    stored = ep.run_execution(repo, argv, target=target, work_item_id=work_item_id,
                             requirement_revision=requirement_revision, fact_path=fact_path)
    if not stored.get("available"):
        raise AssertionError(f"the command could not be run: {stored}")
    doc = dict(stored["receipt"])
    if tests is not None:
        doc["tests"] = tests
    rel = f"{REPORTS_DIR}/{name}"
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": rel, "exit_code": int(stored.get("exit_code", exit_code)),
            "status": str(stored.get("status") or status), "execution_id": str(stored["id"]),
            "receipt": stored["receipt"],
            "digest": hashlib.sha256((repo / rel).read_bytes()).hexdigest()}


def write_report(repo: pathlib.Path, name: str = "unit-tests.json", *, status: str = "passed",
                 exit_code: int = 0, tests: dict | None = None, target: str | None = None,
                 work_item_id: str | None = None, requirement_revision: str | None = None,
                 fact_path: str | None = None, value: bool | str | int | None = None,
                 argv: list[str] | None = None, payload: dict | None = None) -> dict:
    """Write a report the way the verification entry point would produce one.

    What lands on disk is the receipt of a command that ran; `name` is only where it is kept.
    """
    return run_command(repo, name=name, status=status, exit_code=exit_code,
                       target=target or f"gate:{pathlib.Path(name).stem}",
                       work_item_id=work_item_id, requirement_revision=requirement_revision,
                       fact_path=fact_path, value=value, tests=tests, argv=argv, payload=payload)


def forge_report(repo: pathlib.Path, name: str = "unit-tests.json", *, status: str = "passed",
                 exit_code: int = 0, command: str = "python3 -m unittest discover -s tests",
                 tests: dict | None = None) -> dict:
    """A caller-shaped report: the same fields as a receipt, produced by nobody running anything.

    Tests use this to prove that a document which *looks* like a result does not bind one.
    """
    payload = {"status": status, "exit_code": exit_code, "command": command}
    if tests is not None:
        payload["tests"] = tests
    rel = f"{REPORTS_DIR}/{name}"
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": rel, "exit_code": exit_code,
            "digest": hashlib.sha256((repo / rel).read_bytes()).hexdigest()}


def write_approval_event(repo: pathlib.Path, *, approver: str = "reviewer-1", subject: str = "W-1",
                         decision: str = "approved", requirement_revision: str | None = None,
                         fact_path: str | None = None, value: bool | str | int | None = None,
                         event_id: str | None = None,
                         path: str = APPROVAL_EVENTS_REL) -> str:
    """Record the host-side event of a human decision: the channel artifact that is re-read."""
    target = repo / path
    doc: dict = {"events": []}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("events"), list):
                doc = loaded
        except (OSError, ValueError):
            doc = {"events": []}
    event: dict = {"work_item_id": subject, "event": "approval", "id": event_id or f"evt-{len(doc['events']) + 1}",
                   "approver": approver, "subject": subject, "decision": decision,
                   "channel": "host_approval_event"}
    if requirement_revision is not None:
        event["requirement_revision"] = requirement_revision
    if fact_path is not None:
        event["fact_path"] = fact_path
        event["value"] = value
    from trusted_evidence_fixture import sign_event
    event = sign_event(repo, event)
    doc["events"].append(event)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_approval(repo: pathlib.Path, *, approval_id: str = "appr-1", subject: str = "W-1",
                   decision: str = "approved", approver: str = "reviewer-1",
                   requirement_revision: str | None = None, fact_path: str | None = None,
                   value: bool | str | int | None = None, channel: str = "host_approval_event",
                   channel_ref: str | None = None) -> str:
    """Record an approval through the entry point that is the approval's source.

    The decision is appended to the one store the project declares, where it carries the
    receipt of the entry before it, and it names the artifact of the channel it came through.
    Both are re-read when the approval is checked. It binds its own requirement revision: an
    approval that does not say which revision it was issued for cannot be trusted for the
    revision active now, and the outer record must not supply that binding on its behalf.
    """
    requirement_revision = requirement_revision or requirement_dependency(repo)["revision"]
    event = write_approval_event(repo, approver=approver, subject=subject, decision=decision,
                                 requirement_revision=requirement_revision,
                                 fact_path=fact_path, value=value)
    recorded = ep.record_approval(
        repo, approver=approver, subject=subject, work_item_id=subject, decision=decision,
        requirement_revision=requirement_revision, fact_path=fact_path, value=value,
        channel={"type": channel, "ref": channel_ref or event}, approval_id=approval_id)
    if not recorded.get("available"):
        raise AssertionError(f"the approval could not be recorded: {recorded}")
    return ep.APPROVALS_REL


def write_approval_file(repo: pathlib.Path, *, approval_id: str = "appr-1", subject: str = "W-1",
                        decision: str = "approved", approver: str = "reviewer-1",
                        path: str = APPROVALS_REL, requirement_revision: str | None = None,
                        authority: str | None = None) -> str:
    """A caller-shaped approval: a file with an approver's name in it, recorded by nobody.

    Tests use this to prove that copying a valid approval text -- or a valid name -- into some
    other place does not produce a new trusted approval.
    """
    target = repo / path
    doc: dict = {"approvals": []}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("approvals"), list):
                doc = loaded
        except (OSError, ValueError):
            doc = {"approvals": []}
    entry = {"id": approval_id, "subject": subject, "decision": decision, "approver": approver}
    if requirement_revision is not None:
        entry["requirement_revision"] = requirement_revision
    if authority is not None:
        entry["authority"] = authority
    doc["approvals"].append(entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def fact_evidence(repo: pathlib.Path, fact_path: str, *, work_item_id: str | None = None,
                  requirement_revision: str | None = None, producer: str = "observer",
                  value: bool | str | int | None = False, status: str = "passed",
                  exit_code: int = 0) -> dict:
    """Evidence that observed one predicate for real, for facts a search cannot decide.

    "There is no authentication risk" is not the absence of a word, and it is not what some
    other command failing says either. It is carried by an observer that ran for exactly this
    predicate and reported its value: the fact is what the observer said, not a field the
    caller attached to the claim.
    """
    publish_evidence_policy(repo)  # Provision trust before the analysis snapshot is bound.
    work_item_id = work_item_id or work_item_id_for(repo)
    report = write_report(repo, f"fact-{fact_path.replace('.', '-')}.json", status=status,
                          exit_code=exit_code, target=f"fact:{fact_path}",
                          work_item_id=work_item_id, requirement_revision=requirement_revision,
                          fact_path=fact_path, value=value)
    return ep.build_record(
        kind="mechanical_observation", claim_type="fact_observation", work_item_id=work_item_id,
        requirement_revision=requirement_revision, outcome=status, producer=producer,
        source={"type": "path"},
        depends_on=required_dependencies(repo, kind="mechanical_observation",
                                         claim_type="fact_observation"),
        report=report,
    )


def result_document(repo: pathlib.Path, name: str = "gate-result.json", *,
                    status: str = "passed", exit_code: int = 0, target: str | None = None,
                    work_item_id: str | None = None,
                    requirement_revision: str | None = None) -> str:
    """A result document inside the project that a gate, review or verification can bind.

    It is the receipt of a command that ran for the target it is spent on: a gate named `unit`
    is bound to a run produced for `gate:unit`, and nothing else.
    """
    return write_report(repo, name, status=status, exit_code=exit_code,
                        target=target or f"gate:{pathlib.Path(name).stem}",
                        work_item_id=work_item_id,
                        requirement_revision=requirement_revision)["path"]


def build_readiness_record(repo: pathlib.Path, key: str, *, work_item_id: str | None = None,
                           requirement_revision: str | None = None,
                           kind: str | None = None, outcome: str | None = None,
                           producer: str = "test-runner",
                           extra_dependencies: list[dict] | None = None,
                           report_name: str | None = None, report_status: str = "passed",
                           exit_code: int = 0, subject: str | None = None) -> dict:
    """Build a claim that is verifiable because everything it cites exists right now."""
    work_item_id = work_item_id or work_item_id_for(repo)
    kind = kind or READINESS_KINDS.get(key, "mechanical_observation")
    rule = ep.READINESS_SOURCE_RULES.get(key) or {}
    claim_type = str(rule.get("claim_type") or "readiness")
    requirement = requirement_dependency(repo)
    depends_on: list[dict] = required_dependencies(repo, kind=kind, claim_type=claim_type,
                                                   exclude=requirement["object_id"])
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
        # The run is produced for this work item, against this requirement: the receipt carries
        # both, and the claim never fills them in on its behalf.
        report = write_report(repo, report_name, status=report_status, exit_code=exit_code,
                              target=f"readiness:{key}", work_item_id=work_item_id,
                              requirement_revision=requirement_revision)
        record["report"] = report
    if kind == "human_approval":
        approval_id = f"appr-{key}"
        # Who may approve is a project decision, published before the approval is checked.
        publish_evidence_policy(repo)
        source = write_approval(repo, approval_id=approval_id, subject=subject or work_item_id,
                                requirement_revision=requirement_revision)
        record["approval"] = {"source": source, "subject": subject or work_item_id,
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
                    work_item_id: str | None = None, requirement_revision: str | None = None) -> dict:
    """A real observation that tasks were finished: a report that says so, plus its dependencies.

    The count lives in the report, because the report is the source that observed the tasks.
    Metadata beside the claim only declares what the caller expects, and it is checked against
    the report rather than being allowed to replace it.
    """
    work_item_id = work_item_id or work_item_id_for(repo)
    report = write_report(repo, f"tasks-{completed}.json", target="task_progress",
                          work_item_id=work_item_id, requirement_revision=requirement_revision,
                          tests={"completed": completed, "total": total})
    return ep.build_record(
        kind="mechanical_observation", claim_type="task_progress", work_item_id=work_item_id,
        outcome="passed", producer="pytest", requirement_revision=requirement_revision,
        depends_on=required_dependencies(repo, kind="mechanical_observation",
                                         claim_type="task_progress"),
        report=report, source={"type": "test_report", "ref": report["path"]},
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
                       work_item_id: str | None = None, requirement_revision: str | None = None,
                       exit_code: int = 0, status: str = "passed") -> tuple[dict, dict]:
    """A gate may only record passed together with a run that really produced it.

    The gate is bound to the code that was in force while it ran: the conservative snapshot of
    the project tree, which the verifier derives, is part of what the gate result is about.
    """
    work_item_id = work_item_id or work_item_id_for(repo)
    report = write_report(repo, f"{name}.json", status=status, exit_code=exit_code,
                          target=f"gate:{name}", work_item_id=work_item_id,
                          requirement_revision=requirement_revision)
    record = ep.build_record(
        kind="mechanical_observation", claim_type="gate_result", work_item_id=work_item_id,
        requirement_revision=requirement_revision,
        outcome="passed" if status == "passed" else "failed",
        producer="test-runner", source={"type": "path"},
        depends_on=required_dependencies(repo, kind="mechanical_observation",
                                         claim_type="gate_result"),
        report=report,
    )
    return record, report


def code_snapshot_dependency(repo: pathlib.Path) -> dict:
    """The object a result about code is bound to when the project publishes no finer mapping.

    The verifier derives it: the project tree, as it was when the command ran.
    """
    return dependency(repo, ep.DERIVED_CODE_DEPENDENCY, ".")


def required_dependencies(repo: pathlib.Path, *, kind: str = "mechanical_observation",
                          claim_type: str = "readiness", exclude: str | None = None,
                          extra: list | None = None) -> list:
    """The dependencies the verifier requires for this claim, resolved against real objects.

    A claim about code is bound to the code that was in force: where the project publishes no
    finer mapping, that is the conservative snapshot the verifier derives. The caller may add
    objects on top; it may not leave one of these out and still expect to verify.
    """
    required = ep.required_dependencies(kind=kind, claim_type=claim_type)
    deps = [requirement_dependency(repo)]
    if "code_under_test" in required:
        deps.append(code_dependency(repo, exclude=exclude))
    if ep.DERIVED_CODE_DEPENDENCY in required:
        deps.append(code_snapshot_dependency(repo))
    return deps + list(extra or [])
