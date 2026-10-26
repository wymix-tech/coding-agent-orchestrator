"""Project initialization, bootstrap, and host setup commands."""
from __future__ import annotations

import argparse
import json

from scripts.cao_defaults import (
    CONFIG_REL,
    DEFAULT_CONFIG,
    HANDOFF_DIR,
    INDEX_REL,
    SDD_CATALOG,
    SESSION_DIR,
    WORK_DIR,
)
from scripts.cao_guidance import agent_guidance, current_work_action, resume_payload
from scripts.cao_instructions import (
    HOST_ADAPTER_META,
    host_adapter_status,
    install_host_adapters,
    install_project_instruction_templates,
)
from scripts.cao_language import apply_installed_sdd_languages, ensure_language_preference
from scripts.cao_native_sync import reconcile_native_sdd_state
from scripts.cao_providers import (
    detect_current_host,
    detect_sdd,
    host_session_identity,
    sdd_install_instruction,
    sdd_options,
    sdd_status,
    select_sdd_provider,
)
from scripts.cao_runtime import action, config, locked_command, now, repo_root, save_json
from scripts.cao_sessions import register_session
from scripts.cao_work_store import load_index, load_work, save_index, work_summary
from scripts.completion_policy import normalize_scripts
from scripts.provider_restart import restart_state
from scripts.startup_request import ORDER as STARTUP_ORDER, pending_request
from scripts.verification_setup import setup_state, verification_setup_context


@locked_command
def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Return the always-on startup context for an Agent.

    Bootstrap establishes CAO state, verifies that an SDD is installed in the
    project, and only then exposes the Work Item next action. It never creates
    a Work Item and never reruns requirement intake merely because a session or
    Agent changed.
    """
    repo = repo_root(args.repo)
    # Idempotent re-check: covers projects provisioned before this step existed,
    # and files the user deleted. Never overwrites an existing file.
    project_instructions = install_project_instruction_templates(repo, getattr(args, "host", None) or detect_current_host())
    if not (repo / CONFIG_REL).exists():
        save_json(repo / CONFIG_REL, json.loads(json.dumps(DEFAULT_CONFIG)))
        save_index(repo, load_index(repo))
        for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
            (repo / d).mkdir(parents=True, exist_ok=True)

    ensure_language_preference(repo)
    status = sdd_status(repo)
    cfg = config(repo)
    selected = status["selected"]
    installed = status["installed"]
    active_id = load_index(repo).get("active_work")
    work = load_work(repo, active_id) if active_id else None
    bmad = next((item for item in status['providers'] if item['provider'] == 'bmad' and item.get('detected')), None)
    if bmad and not bmad.get('healthy', True) and restart_state(repo, getattr(args, 'host', None) or detect_current_host())['ready'] and (selected == 'bmad' or (work or {}).get('sdd', {}).get('provider') == 'bmad'):
        print(json.dumps({'ok': False, 'mode': 'sdd_install_required', 'sdd': bmad,
            'work_item': work_summary(work) if work else None,
            'next': action('agent', 'Repair the selected stable BMAD installation with `cao sdd install --provider bmad --host <host> --yes`, then bootstrap again. Keep the current Work and confirmed project decisions.', reason='SDD_INSTALL_UNHEALTHY')}, ensure_ascii=False)); return 2
    # Bootstrap is a state-read boundary too. Reconcile native SDD state before
    # constructing a new-session context so a previous Agent's phase cursor
    # cannot remain stale after the native workflow has advanced.
    if (work and work.get("status") in {"active", "suspended"}
            and restart_state(repo, getattr(args, "host", None) or detect_current_host())["ready"]
            and reconcile_native_sdd_state(repo, work)):
        work = load_work(repo, active_id) or work

    # Session continuity must not depend solely on the host adapter being
    # installed. When the runtime exposes a host and a native session id, attach
    # the Session here as well so `cao sessions list` is never empty.
    identity = host_session_identity(getattr(args, "host", None))
    bootstrap_session = register_session(repo, work, identity[1], identity[0], event="bootstrap") if identity else None

    # Report, do not install. Adapters load when the host process starts, so a
    # bootstrap-time install could never deliver this session's SessionStart.
    adapter_host = identity[0] if identity else getattr(args, "host", None)
    bootstrap_adapter = host_adapter_status(repo, adapter_host) if adapter_host else None

    restart = restart_state(repo, adapter_host or detect_current_host())
    if not restart["ready"]:
        next_action = action("human", restart["next"], reason=restart["reason"], stage="startup")
        print(json.dumps({"ok": True, "mode": "host_restart_required", "restart": restart,
                          "work_item": {**work_summary(work), "next_action": next_action} if work else None,
                          "next": next_action,
                          "host_adapter": bootstrap_adapter, "project_instructions": project_instructions,
                          "guidance": agent_guidance(repo, work, "host_restart_required")}, ensure_ascii=False, indent=2))
        return 0

    # Existing work owns its provider choice. A missing provider is repaired
    # before any resume/implementation action is exposed.
    work_provider = str(((work or {}).get("sdd") or {}).get("provider") or "auto")
    if work and work_provider in SDD_CATALOG and not detect_sdd(repo, work_provider)["installed"]:
        next_out = {
            "ok": True,
            "mode": "sdd_install_required",
            "work_item": work_summary(work),
            "sdd": {"selected": work_provider, "status": detect_sdd(repo, work_provider)},
            "options": sdd_options(status),
            "next": action("human", f"SDD `{work_provider}` is not installed in this project. Run `{sdd_install_instruction(work_provider, getattr(args, 'host', None))}`, then run `cao bootstrap` again."),
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    if not installed:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "not_installed", "selected": selected},
            "selection_required": True,
            "selection_policy": "explicit_user_choice_only",
            "startup_order": STARTUP_ORDER,
            "pending_request": pending_request(repo) or None,
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: no SDD is installed and no provider is authorized. Ask the user to explicitly choose OpenSpec, BMad Method, or GitHub Spec Kit and wait for their answer. Do not infer a choice from recommendations, popularity, existing global CLIs, Agent preference, or task type. Do not run any provider install/select command before the user answers. After an explicit answer only, run `cao sdd install --provider <chosen-provider> --yes`. The installer must detect the active host from the current Agent runtime and install only that host's integration. If detection is unavailable or ambiguous, stop and request `--host` (or a provider-specific explicit integration/tools option); never use an all-hosts default. If the interaction channel cannot ask the user, stop with SDD_SELECTION_REQUIRED. Do not start or modify production code before installation completes."),
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    # The configured user language applies to every installed SDD, regardless
    # of which provider is selected for the current Work Item.
    language_configuration = apply_installed_sdd_languages(repo)
    if language_configuration["failures"]:
        failed = {
            "ok": False,
            "mode": "language_configuration_failed",
            "error": "SDD_LANGUAGE_CONFIGURATION_FAILED",
            "language_configuration": language_configuration,
            "next": action("human", "Repair the reported SDD language configuration, then rerun `cao bootstrap`."),
            "project_instructions": project_instructions,
        }
        failed["guidance"] = agent_guidance(repo, work, failed["mode"])
        print(json.dumps(failed, ensure_ascii=False, indent=2))
        return 2

    # If exactly one SDD is already installed, make it the project selection
    # automatically. Multiple installed SDDs require an explicit choice.
    if selected == "auto" and len(installed) == 1:
        select_sdd_provider(repo, installed[0]["provider"])
        selected = installed[0]["provider"]
    elif selected == "auto" and len(installed) > 1:
        next_out = {
            "ok": True,
            "mode": "sdd_select",
            "work_item": work_summary(work) if work else None,
            "sdd": {"status": "multiple_installed", "selected": "auto"},
            "selection_required": True,
            "selection_policy": "explicit_user_choice_only",
            "startup_order": STARTUP_ORDER,
            "pending_request": pending_request(repo) or None,
            "options": sdd_options(status),
            "next": action("human", "HARD STOP: multiple SDDs are installed and no provider is authorized. Ask the user to explicitly choose one and wait for their answer. Do not infer a choice from recommendations, task type, or Agent preference. Only after the user answers, run `cao sdd select --provider <chosen-provider>` and bootstrap again. If unable to ask, stop with SDD_SELECTION_REQUIRED."),
            "language_configuration": language_configuration,
        }
        next_out["project_instructions"] = project_instructions
        next_out["guidance"] = agent_guidance(repo, work, next_out["mode"])
        next_out['next']['instruction'] += ' ' + STARTUP_ORDER
        print(json.dumps(next_out, ensure_ascii=False, indent=2))
        return 0

    inspection = setup_state(cfg)
    current = current_work_action(repo, work)
    mode = "project_context_setup" if current.get("stage") == "project_setup" else "verification_setup" if current.get("stage") == "verification_setup" else "resume" if work else "select"
    if current.get("stage") == "project_setup" and current.get("reason") in {"PROJECT_VERIFICATION_PLAN_REQUIRED", "PROJECT_VERIFICATION_CONTEXT_MISMATCH"}:
        mode = "project_verification_plan"
    next_out = {
        "ok": True, "mode": mode,
        "pending_request": pending_request(repo) if not work else None,
        "work_item": work_summary(work) if work else None,
        "sdd": sdd_status(repo),
        "context": verification_setup_context(work) if mode == "verification_setup" else resume_payload(repo, work, next_action=current) if work else None,
        "next": current,
    }
    if next_out["work_item"]:
        next_out["work_item"]["next_action"] = current
    next_out["verification_setup"] = inspection
    from scripts.project_context import context_state
    next_out["project_context"] = context_state(repo, work=work)
    if bootstrap_session:
        next_out["session"] = bootstrap_session
    if bootstrap_adapter is not None:
        next_out["host_adapter"] = bootstrap_adapter
        if not bootstrap_adapter["installed"]:
            next_out["host_adapter"]["next"] = f"cao host install --host {adapter_host}  (takes effect from the next session)"
    next_out["language_configuration"] = language_configuration
    next_out["project_instructions"] = project_instructions
    next_out["guidance"] = agent_guidance(repo, work, next_out["mode"], next_action=current)
    print(json.dumps(next_out, ensure_ascii=False, indent=2))
    return 0


@locked_command
def _initialize_state(args: argparse.Namespace):
    repo = repo_root(args.repo)
    cfg_path = repo / CONFIG_REL
    if cfg_path.exists() and not args.force:
        out = {"ok": True, "status": "already_initialized", "config": str(cfg_path)}
        pending = [h for h in HOST_ADAPTER_META if not host_adapter_status(repo, h)["installed"]]
        if pending:
            out["host_adapters"] = {"not_installed": pending, "next": f"cao host install --host {'|'.join(pending)}"}
        print(json.dumps(out, ensure_ascii=False, indent=2)); return 0

    # Host adapters are process-startup integrations, so they belong to
    # provisioning. The host must be explicit: guessing here would silently wire
    # the wrong host's hooks. An unknown host is reported as an unresolved
    # adapter instead of failing initialization, so a plain-shell `cao init`
    # (and CI) still works while the hook gap stays visible.
    host = args.host or detect_current_host()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if args.sdd:
        cfg["sdd"]["provider"] = args.sdd
    if getattr(args, "language", None):
        cfg.setdefault("language", {})["user"] = args.language.strip()
    verify_scripts = args.verify_script or args.verify_command or []
    test_scripts = getattr(args, "test_script", None) or []
    if verify_scripts or test_scripts:
        general = [{"name": f"script-{i}", "command": str(command).strip(), "required": True}
                   for i, command in enumerate(verify_scripts, 1)]
        tests = [{"name": f"test-{i}", "command": str(command).strip(), "required": True, "gates": ["tests"]}
                 for i, command in enumerate(test_scripts, 1)]
        cfg["verification"]["scripts"] = normalize_scripts(general + tests)
        # Informational time only; accepted configuration hashes are authoritative.
        cfg["verification"]["configured_at"] = now()
    save_json(cfg_path, cfg)
    index = load_index(repo)
    save_index(repo, index)
    for d in (WORK_DIR, SESSION_DIR, HANDOFF_DIR):
        (repo / d).mkdir(parents=True, exist_ok=True)
    return repo, cfg_path, host


def cmd_init(args: argparse.Namespace) -> int:
    prepared = _initialize_state(args)
    if isinstance(prepared, int):
        return prepared
    repo, cfg_path, host = prepared
    # Host installation may run an external package manager; never hold state locks.
    if host:
        adapters = install_host_adapters(repo, [host])
    else:
        adapters = {
            "hosts": [],
            "installed": [],
            "already_present": [],
            "failed": [],
            "status": "host_required",
        }
    project_instructions = install_project_instruction_templates(repo, host)
    out = {"ok": True, "status": "initialized", "config": str(cfg_path), "state": str(repo / INDEX_REL), "host": host, "host_adapters": adapters, "project_instructions": project_instructions}
    if not host:
        out["host_adapters"]["next"] = f"cao host install --host {'|'.join(sorted(HOST_ADAPTER_META))}"
        out["warning"] = (
            "No coding-agent host could be determined, so no host adapter was installed. Host events (Session, Handoff, completion gate) will not fire. "
            "CAO's bootstrap/start Session fallback still covers the current session."
        )
    elif adapters["failed"]:
        out["warning"] = (
            f"Host adapter for `{host}` could not be installed. Host events (Session, Handoff, completion gate) will not fire in this project "
            "for hosts started after this point. CAO's bootstrap/start Session fallback still covers the current session."
        )
    print(json.dumps(out, ensure_ascii=False, indent=2)); return 0


def cmd_host(args: argparse.Namespace) -> int:
    """Wire or inspect host adapters for an already-initialized project."""
    repo = repo_root(args.repo)
    if args.host_command == "install":
        host = args.host or detect_current_host()
        if not host:
            print(json.dumps({
                "ok": False,
                "error": "HOST_REQUIRED",
                "reason": "Cannot determine the coding-agent host from this environment.",
                "hosts": sorted(HOST_ADAPTER_META),
                "next": "cao host install --host <claude-code|codex|pi>",
            }, ensure_ascii=False, indent=2)); return 2
        adapters = install_host_adapters(repo, [host])
        print(json.dumps({"ok": True, "host": host, "host_adapters": adapters}, ensure_ascii=False, indent=2))
        return 0 if not adapters["failed"] else 1
    hosts = [args.host] if args.host else sorted(HOST_ADAPTER_META)
    print(json.dumps({
        "ok": True,
        "hosts": [host_adapter_status(repo, host) for host in hosts],
        "note": "Host adapters load when the host process starts, so install them before the next session.",
    }, ensure_ascii=False, indent=2))
    return 0
