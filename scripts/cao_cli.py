"""Argument parser, command dispatch, and CLI error rendering."""
from __future__ import annotations

import argparse
import json

from scripts.cao_artifacts import (
    cmd_artifact_add,
    cmd_artifact_confirm,
    cmd_artifact_list,
    cmd_artifact_refresh,
    cmd_artifact_remove,
)
from scripts.cao_bootstrap import cmd_bootstrap, cmd_host, cmd_init
from scripts.cao_defaults import ACTION_TYPES, EXECUTION_PROFILES, PATH_RISK_MINIMUM, SDD_CATALOG
from scripts.cao_host_events import host_event
from scripts.cao_instructions import HOST_ADAPTER_META, cmd_instructions_update
from scripts.cao_language import cmd_language_apply, cmd_language_set, cmd_language_show
from scripts.cao_native_sync import (
    cmd_lifecycle_events,
    cmd_sdd_approval,
    cmd_sdd_artifact_bind,
    cmd_sdd_route,
    cmd_sdd_sync,
)
from scripts.cao_path import cmd_gate, cmd_path_assess, cmd_path_show
from scripts.cao_plans import cmd_sdd_plan_bind
from scripts.cao_providers import cmd_sdd_install, cmd_sdd_list, cmd_sdd_select, detect_current_host
from scripts.cao_requirements import cmd_requirement_list, cmd_requirement_next_id
from scripts.cao_runtime import action, repo_root
from scripts.cao_sessions import cmd_sessions_list, cmd_sessions_show
from scripts.cao_verification import (
    cmd_sdd_acceptance,
    cmd_verification_configure,
    cmd_verification_smoke,
    cmd_verify,
)
from scripts.cao_work_commands import (
    cmd_block,
    cmd_checkpoint,
    cmd_context,
    cmd_decision,
    cmd_decision_list,
    cmd_done,
    cmd_handoff,
    cmd_list,
    cmd_next,
    cmd_reopen,
    cmd_resume,
    cmd_start,
    cmd_status,
    cmd_unblock,
)
from scripts.cao_workflow_commands import cmd_sdd_review, cmd_sdd_spec, cmd_sdd_workflow
from scripts.provider_restart import restart_state
from scripts.runtime_commands import cmd_runtime, register_parser as register_runtime_parser


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the read-only local orchestration dashboard."""
    from scripts.dashboard import serve_dashboard
    return int(serve_dashboard(args))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cao", description="Coding Agent Orchestrator")
    p.add_argument("--compact-json", action="store_true", help="Compact JSON; preserves errors and exit status (accepted anywhere)")
    sub = p.add_subparsers(dest="command", required=True)
    register_runtime_parser(sub)
    from scripts.conformance import register_parser as register_conformance_parser
    register_conformance_parser(sub)
    from scripts.knowledge_commands import register_parser as register_knowledge_parser
    register_knowledge_parser(sub)
    from scripts.project_commands import register_parser as register_project_parser
    register_project_parser(sub)
    from scripts.acceptance_summary import register_parser as register_acceptance_parser
    register_acceptance_parser(sub)
    sp = sub.add_parser("init"); sp.add_argument("--repo", default="."); sp.add_argument("--sdd", default=None); sp.add_argument("--language", default=None, help="User language / locale for conversation and all SDD artifacts, e.g. zh-CN or en-US"); sp.add_argument("--verify-script", action="append", default=None, help="Project verification script command; repeat for multiple scripts"); sp.add_argument("--test-script", action="append", default=None, help="Required verification script declaring the tests gate; repeat for multiple test suites"); sp.add_argument("--verify-command", action="append", default=None, help=argparse.SUPPRESS); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Coding-agent host whose hooks are wired during initialization"); sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=cmd_init)
    host = sub.add_parser("host", help="Wire or inspect the coding-agent host hooks for this project")
    host_sub = host.add_subparsers(dest="host_command", required=True)
    sp = host_sub.add_parser("install", help="Install the host adapter (SessionStart/PreToolUse/Stop hooks)")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Host to wire; auto-detected from the environment when omitted")
    sp.set_defaults(fn=cmd_host)
    sp = host_sub.add_parser("status", help="Show whether each host adapter is wired")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=sorted(HOST_ADAPTER_META), default=None, help="Host to inspect; defaults to all")
    sp.set_defaults(fn=cmd_host)
    sp = sub.add_parser("bootstrap"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default=None, choices=["claude-code", "codex", "pi"]); sp.set_defaults(fn=cmd_bootstrap)
    sp = sub.add_parser("dashboard", help="Launch the read-only local orchestration dashboard")
    sp.add_argument("--repo", default=".", help="Project repository root")
    sp.add_argument("--host", default="127.0.0.1", help="Bind address; non-loopback requires --allow-remote")
    sp.add_argument("--port", type=int, default=8765, help="HTTP port (0 selects an available port)")
    sp.add_argument("--refresh", type=int, default=10, help="Browser refresh interval in seconds, minimum 3")
    sp.add_argument("--open", action="store_true", help="Open the dashboard URL in a browser")
    sp.add_argument("--allow-remote", action="store_true", help="Allow binding beyond localhost; exposes project metadata without authentication")
    sp.set_defaults(fn=cmd_dashboard)
    language = sub.add_parser("language", help="Configure user communication and SDD artifact language")
    language_sub = language.add_subparsers(dest="language_command", required=True)
    sp = language_sub.add_parser("show", help="Show the configured user language and installed SDDs")
    sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_language_show)
    sp = language_sub.add_parser("set", help="Set language and apply it to every installed SDD")
    sp.add_argument("--repo", default="."); sp.add_argument("--language", required=True, help="Locale or language name, e.g. zh-CN, en-US, Japanese")
    sp.set_defaults(fn=cmd_language_set)
    sp = language_sub.add_parser("apply", help="Reapply the configured language to installed SDDs")
    sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG), help="Apply only to one provider; default applies to all installed SDDs")
    sp.set_defaults(fn=cmd_language_apply)
    sdd = sub.add_parser("sdd", help="Detect, select, and install a project SDD")
    sdd_sub = sdd.add_subparsers(dest="sdd_command", required=True)
    route = sdd_sub.add_parser("route", help="Record evidence-backed native routes separately from CAO quality profiles")
    route_sub = route.add_subparsers(dest="route_action", required=True)
    for name in ("propose", "show", "prepare", "rule", "check"):
        sp = route_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name in {"propose", "check"}: sp.add_argument("--declaration", required=name == "propose", metavar="JSON_FILE", help="Existing declaration JSON path; new inputs use .orchestrator/inputs/WORK_ID/route.json")
        if name == "propose": sp.add_argument("--repair-evidence", action="store_true", help="Relocate byte-equivalent rule/contract evidence without resetting intent approval or execution")
        if name == "prepare": sp.add_argument("--from", dest="from_declaration", metavar="JSON_FILE", help="Copy an existing declaration into the new input location without overwriting a current draft")
        if name == "rule":
            sp.add_argument("--source", required=True, help="Actual installed native rule file")
            sp.add_argument("--start-line", required=True, type=int)
            sp.add_argument("--end-line", required=True, type=int)
            sp.add_argument("--index", type=int, help="Zero-based rule index to replace; default replaces the same source path or appends")
        sp.set_defaults(fn=cmd_sdd_route)
    artifact = sdd_sub.add_parser("artifact", help="Bind a running native artifact without certifying completion")
    artifact_sub = artifact.add_subparsers(dest="artifact_action", required=True)
    sp = artifact_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--artifact", required=True)
    sp.set_defaults(fn=cmd_sdd_artifact_bind)
    approval = sdd_sub.add_parser("approval", help="Request native SPEC approval and inspect host-observed evidence")
    approval_sub = approval.add_subparsers(dest="approval_action", required=True)
    for name in ("request", "show"):
        sp = approval_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        sp.set_defaults(fn=cmd_sdd_approval)
    sp = sdd_sub.add_parser("sync", help="Synchronize native progress and implementation blockers")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_sdd_sync)
    review = sdd_sub.add_parser("review", help="Record independent native reviews and pre-patch dispatch classification")
    review_sub = review.add_subparsers(dest="review_action", required=True)
    for name in ("begin", "attach", "submit", "classify", "resolve", "findings", "prepare", "import", "status", "check", "draft", "classify-batch", "loopback"):
        sp = review_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name not in {'findings', 'prepare', 'status', 'classify-batch', 'loopback'}:
            sp.add_argument("--layer", required=True)
        if name in {"begin", "prepare"}:
            sp.add_argument("--model", help="Explicit supported reviewer model; default is the current host-observed coordinator model"); sp.add_argument("--instructions", required=True)
            sp.add_argument('--restart', action='store_true', help='Explicitly replace a failed current dispatch ticket')
            if name == 'prepare':
                capability = sp.add_mutually_exclusive_group()
                capability.add_argument('--agent-definition', help='Actual installed agent definition under .pi/agents or .agents/agents')
                capability.add_argument('--external-session', action='store_true', help='Prepare independent external-host handoff; adapter is verified on attach')
        elif name == 'findings':
            sp.add_argument('--source', required=True, help='Unmodified native Markdown list or JSON findings under _bmad-output')
        elif name == 'draft':
            sp.add_argument('--source', required=True, help='Original native Markdown/JSON findings; generates triage skeleton')
        elif name == 'loopback':
            sp.add_argument('--extend', action='store_true', help='Record additional exact files discovered during the active native replan; preserves the old round')
            sp.add_argument('--reason', required=True)
            sp.add_argument('--keep', required=True, help='Native KEEP instructions retained before reverting')
            sp.add_argument('--path', action='append', required=True, help='Exact product file to revert/reimplement; repeat for all affected files')
        elif name == 'status':
            pass
        elif name == 'check':
            sp.add_argument('--reviewer-session', help='Coordinator runs a check for its bound observed reviewer child')
            sp.add_argument('--script', required=True, help='Configured verification script name; executes in a temporary copy')
        elif name == 'attach':
            sp.add_argument('--reviewer-session', help='Coordinator binds an observed read-only child before review')
            sp.add_argument('--run', required=True); sp.add_argument('--input-sha256', required=True)
            sp.add_argument('--model', required=True)
        else:
            sp.add_argument("--report", required=True)
        sp.set_defaults(fn=cmd_sdd_review)
    acceptance = sdd_sub.add_parser("acceptance", help="Independent SDD final acceptance for full paths")
    acceptance_sub = acceptance.add_subparsers(dest="acceptance_action", required=True)
    for name in ("configure", "show", "run", "begin", "submit", "protocol"):
        sp = acceptance_sub.add_parser(name)
        sp.add_argument("--repo", default="."); sp.add_argument("--work")
        if name == "configure":
            sp.add_argument("--declaration", required=True); sp.add_argument("--replace", action="store_true")
        if name == "submit":
            sp.add_argument("--run", required=True); sp.add_argument("--report", required=True); sp.add_argument("--exit-code", type=int, default=0)
        sp.set_defaults(fn=cmd_sdd_acceptance)
    spec = sdd_sub.add_parser("spec", help="Bind a minimal native SPEC and explicit criteria")
    spec_sub = spec.add_subparsers(dest="spec_command", required=True)
    sp = spec_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--spec", required=True)
    sp.add_argument("--criteria"); sp.add_argument("--criteria-section"); sp.add_argument("--replace", action="store_true")
    sp.set_defaults(fn=cmd_sdd_spec)
    plan = sdd_sub.add_parser("plan", help="Associate native execution items with this requirement")
    plan_sub = plan.add_subparsers(dest="plan_action", required=True)
    sp = plan_sub.add_parser("bind")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.add_argument("--plan", action="append", help="Associated native task file or quoted OpenSpec tracked-file glob; repeat for ALL changes/features")
    sp.add_argument("--plan-item", action="append", help="BMAD plan path#story-id or path#epic-N; repeat for ALL requirement items; adds to existing scope")
    sp.set_defaults(fn=cmd_sdd_plan_bind)
    sp = sdd_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_sdd_list)
    sp = sdd_sub.add_parser("select"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", required=True, choices=sorted(SDD_CATALOG)); sp.set_defaults(fn=cmd_sdd_select)
    sp = sdd_sub.add_parser("install"); sp.add_argument("--repo", default="."); sp.add_argument("--provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--tools", default=None, help="OpenSpec init tool ids, or all/none"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used by Spec Kit to select its integration"); sp.add_argument("--integration", default=None, help="Spec Kit integration key, e.g. claude, codex, pi, copilot"); sp.add_argument("--integration-options", default=None, help="Spec Kit integration options passed through as one value"); sp.add_argument("--yes", action="store_true", help="Confirm installation of external/project software"); sp.set_defaults(fn=cmd_sdd_install)
    sp.add_argument("--upgrade", action="store_true", help="Explicitly upgrade the machine CLI instead of reusing an installed OpenSpec CLI")
    workflow = sdd_sub.add_parser("workflow", help="Record and enforce native SDD workflow execution")
    workflow_sub = workflow.add_subparsers(dest="workflow_action", required=True)
    sp = workflow_sub.add_parser("select", help="Record the provider-native workflow before executing it")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--name", required=True); sp.add_argument("--entrypoint", required=True)
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented exception when the entrypoint cannot be verified; requires --reason")
    sp.add_argument("--reason", default=None, help="Why this workflow cannot be verified natively; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    sp = workflow_sub.add_parser("execute", help="Record that the selected native workflow was actually executed")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--command", required=True, help="The command or skill invocation that was actually run")
    sp.add_argument("--exit-code", type=int, default=0, help="Exit code returned by the executed workflow")
    sp.add_argument("--output", default=None, help="Tail of the workflow output to keep as evidence")
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented exception; requires --reason")
    sp.add_argument("--reason", default=None, help="Why the executed command cannot be verified; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    sp = workflow_sub.add_parser("complete", help="Record native workflow completion and actual output files")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--artifact", action="append", required=True, help="Existing repository-relative output file; repeat for each file")
    plan = sp.add_mutually_exclusive_group()
    plan.add_argument("--plan", action="append", help="Associated plan path; repeat for related changes/features")
    plan.add_argument("--no-plan", action="store_true", help="Explicitly declare that this workflow produces no plan")
    sp.add_argument("--plan-item", action="append", help="Shared plan path#story-id or path#epic-N; repeat for every associated story/epic")
    sp.add_argument("--allow-unverified", action="store_true", help="Record a documented native-entrypoint or failed-execution exception; output ownership is still required")
    sp.add_argument("--reason", default=None, help="Why this completion needs an exception; required with --allow-unverified")
    sp.set_defaults(fn=cmd_sdd_workflow)
    lifecycle = sub.add_parser("lifecycle", help="Inspect persisted CAO lifecycle events")
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_command", required=True)
    sp = lifecycle_sub.add_parser("events", help="List lifecycle events from native SDD reconciliation and CAO transitions")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--limit", type=int, default=100); sp.set_defaults(fn=cmd_lifecycle_events)
    sp = sub.add_parser("start"); sp.add_argument("--repo", default="."); sp.add_argument("--requirement"); sp.add_argument("--title", default=None, help="Human-readable Work Item title; overrides the requirement filename"); sp.add_argument("--sdd-ref"); sp.add_argument("--sdd-provider", choices=sorted(SDD_CATALOG)); sp.add_argument("--host", choices=["claude-code", "codex", "pi"], default=None, help="Current coding-agent host; used to attach the runtime Session"); sp.add_argument("--replace", action="store_true"); sp.add_argument("--reopen", action="store_true"); sp.add_argument("--scope", action="append"); sp.set_defaults(fn=cmd_start)
    path = sub.add_parser("path", help="Assess and inspect the minimum-safe execution path")
    path_sub = path.add_subparsers(dest="path_command", required=True)
    sp = path_sub.add_parser("assess", help="Record an Agent's lightweight scope/risk assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.add_argument("--profile", required=True, choices=EXECUTION_PROFILES)
    sp.add_argument("--reason", required=True, help="Short explanation for the selected profile")
    risk_cli_names = sorted(set(PATH_RISK_MINIMUM) | {name.replace("_", "-") for name in PATH_RISK_MINIMUM})
    sp.add_argument("--risk-flag", action="append", default=[], choices=risk_cli_names, help="Risk requiring a minimum profile; repeat as needed")
    sp.add_argument("--question", action="append", default=[], help="Unresolved requirement question; repeat as needed")
    sp.add_argument("--assessed-by", default="agent", help="Who performed the assessment")
    sp.set_defaults(fn=cmd_path_assess)
    sp = path_sub.add_parser("show", help="Show the current Work Item's path assessment")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.set_defaults(fn=cmd_path_show)
    gate = sub.add_parser("gate", help="Satisfy and inspect the execution gates of the selected path")
    gate_sub = gate.add_subparsers(dest="gate_command", required=True)
    sp = gate_sub.add_parser("satisfy", help="Record that a required gate has actually been satisfied")
    sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--gate", required=True, help="Gate name from `cao path show`, e.g. scope_review")
    sp.add_argument("--evidence", required=True, help="What was actually done to satisfy this gate")
    sp.add_argument("--satisfied-by", default="agent", help="Who satisfied the gate")
    sp.set_defaults(fn=cmd_gate)
    sp = gate_sub.add_parser("show", help="Show the required gates and their current status")
    sp.add_argument("--repo", default="."); sp.add_argument("--work")
    sp.set_defaults(fn=cmd_gate)
    artifact = sub.add_parser("artifact", help="Manage SDD artifact references")
    artifact_sub = artifact.add_subparsers(dest="artifact_command", required=True)
    sp = artifact_sub.add_parser("add"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.add_argument("--role"); sp.add_argument("--native-type", dest="native_type"); sp.add_argument("--native-ref", dest="native_ref"); sp.set_defaults(fn=cmd_artifact_add)
    sp = artifact_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_artifact_list)
    sp = artifact_sub.add_parser("confirm", help="Mark an SDD artifact as reviewed and confirmed"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.add_argument("--confirmed-by", default="user"); sp.set_defaults(fn=cmd_artifact_confirm)
    sp = artifact_sub.add_parser("remove"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path", required=True); sp.set_defaults(fn=cmd_artifact_remove)
    sp = artifact_sub.add_parser("refresh"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--path"); sp.set_defaults(fn=cmd_artifact_refresh)
    sp = sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.set_defaults(fn=cmd_list)
    sp = sub.add_parser("status"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_status)
    instructions = sub.add_parser("instructions", help="Preview or apply a safe CAO project-instruction upgrade")
    instructions_sub = instructions.add_subparsers(dest="instructions_command", required=True)
    sp = instructions_sub.add_parser("update", help="Preview managed/known legacy instructions; --apply backs up and replaces only CAO content")
    sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=["claude-code", "codex", "pi"])
    sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_instructions_update)
    sp = sub.add_parser("next"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_next)
    sp = sub.add_parser("resume"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--host", default="unknown"); sp.add_argument("--session"); sp.add_argument("--reactivate", action="store_true"); sp.set_defaults(fn=cmd_resume)
    sp = sub.add_parser("checkpoint"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--step"); sp.add_argument("--next"); sp.add_argument("--next-type", choices=sorted(ACTION_TYPES), default="agent"); sp.add_argument("--command"); sp.add_argument("--summary"); sp.add_argument("--completed"); sp.add_argument("--pending"); sp.set_defaults(fn=cmd_checkpoint)
    sp = sub.add_parser("decision"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--decision", required=True); sp.add_argument("--reason"); sp.add_argument("--scope"); sp.add_argument("--supersedes"); sp.set_defaults(fn=cmd_decision)
    sp = sub.add_parser("block"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", required=True); sp.add_argument("--resolution"); sp.add_argument("--suspend", action="store_true"); sp.set_defaults(fn=cmd_block)
    sp = sub.add_parser("unblock"); sp.add_argument("--repo", default="."); sp.add_argument("--work", required=True); sp.add_argument("--blocker"); sp.set_defaults(fn=cmd_unblock)
    sp = sub.add_parser("handoff", help="Create or inspect Handoff records"); sp.add_argument("handoff_action", nargs="?", choices=["list", "show"]); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host"); sp.add_argument("--reason", default="manual checkpoint"); sp.add_argument("--summary"); sp.add_argument("--end-session", action="store_true", help="End the referenced Session after creating the handoff"); sp.add_argument("--id", dest="handoff_id"); sp.add_argument("--limit", type=int, default=20); sp.set_defaults(fn=cmd_handoff)
    sp = sub.add_parser("decisions", help="List recorded decisions for a Work Item"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--limit", type=int, default=50); sp.set_defaults(fn=cmd_decision_list)
    sessions = sub.add_parser("sessions", help="Inspect Agent Sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sp = sessions_sub.add_parser("list"); sp.add_argument("--repo", default="."); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--work"); sp.add_argument("--status", choices=["active", "ended"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_list)
    sp = sessions_sub.add_parser("show"); sp.add_argument("--repo", default="."); sp.add_argument("--id", required=True); sp.add_argument("--host", choices=["claude-code", "codex", "pi"]); sp.add_argument("--stale-after-minutes", type=int, default=30); sp.set_defaults(fn=cmd_sessions_show)
    verification = sub.add_parser("verification", help="Configure project verification without resetting other state")
    verification_sub = verification.add_subparsers(dest="verification_command", required=True)
    from scripts.verification_probe import command as cmd_verification_check
    probe = verification_sub.add_parser('check', help='Run configured preparation checks without certifying final acceptance')
    probe.add_argument('--repo', default='.')
    probe.add_argument('--work')
    probe.set_defaults(fn=cmd_verification_check)
    sp = verification_sub.add_parser("smoke", help="HTTP smoke with a fresh port and owned server process")
    sp.add_argument("--repo", default="."); sp.add_argument("--server-command", required=True)
    sp.add_argument("--path", default="/"); sp.add_argument("--method", choices=["GET", "POST"], default="GET")
    sp.add_argument("--body"); sp.add_argument("--expect-status", type=int, default=200)
    sp.add_argument("--timeout", type=float, default=10)
    sp.set_defaults(fn=cmd_verification_smoke)
    sp = verification_sub.add_parser("configure", help="Declare required project scripts without installing or executing anything")
    sp.add_argument("--work", help="Work to inspect for reanchor; scripts remain project-wide")
    sp.add_argument("--repo", default="."); sp.add_argument("--test-script", action="append"); sp.add_argument("--verify-script", action="append")
    sp.add_argument("--output", action="append", help="Generated verification output: root-relative file or directory ending in /; repeat to replace the output list")
    sp.add_argument("--exclude-path", action="append", help="Root-relative generated/cache path to exclude; Git tracked inputs always override it")
    sp.add_argument("--clear-exclude-paths", action="store_true", help="Explicitly remove snapshot path exclusions")
    sp.add_argument("--clear-outputs", action="store_true", help="Explicitly remove all verification output declarations")
    sp.add_argument("--security-script", action="append", help="Required security checks; reuse the same test wrapper to declare both capabilities without running it twice")
    sp.add_argument("--replace", action="store_true", help="Explicitly replace different existing verification declarations")
    sp.add_argument("--environment-variable", action="append", help="Bind hashes of relevant environment variables; values are never stored")
    sp.add_argument("--timeout-seconds", type=float, help="Positive verification timeout; requires --replace when changed")
    sp.add_argument("--maximum-evidence-age-seconds", type=float, help="Accepted positive validity period for technical evidence")
    sp.add_argument("--plan-only", action="store_true", help="Save a non-executable plan before a Work exists")
    sp.add_argument("--scope", action="append", help="Affected project module; repeat for shared verification")
    sp.set_defaults(fn=cmd_verification_configure)
    sp = sub.add_parser("verify"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--script", action="append", help="Engineering diagnostic only, never grants done eligibility; repeat for multiple scripts"); sp.add_argument("--command", action="append", help=argparse.SUPPRESS); sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("done"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.set_defaults(fn=cmd_done)
    sp = sub.add_parser("reopen", help="Reopen a completed Work Item that should not have been closed"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--reason", help="Why the completion was wrong, e.g. which part of the requirement is missing"); sp.add_argument("--reanchor", action="store_true", help="Explicitly accept the current verification standard and clear gates/verification; also supports active Work Items"); sp.set_defaults(fn=cmd_reopen)
    sp = sub.add_parser("context"); sp.add_argument("--repo", default="."); sp.add_argument("--work"); sp.add_argument("--session"); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=cmd_context)
    requirement = sub.add_parser("requirement", help="Allocate numbered CAO requirement documents")
    requirement_sub = requirement.add_subparsers(dest="requirement_action")
    sp = requirement_sub.add_parser("next-id", help="Reserve the next numbered requirement path"); sp.add_argument("--repo", default="."); sp.add_argument("--title", help="Requirement title, used for the filename slug"); sp.add_argument("--date", help="Override the date segment (YYYY-MM-DD)"); sp.set_defaults(fn=cmd_requirement_next_id)
    sp = requirement_sub.add_parser("list", help="List CAO requirement documents"); sp.add_argument("--repo", default="."); sp.add_argument("--limit", type=int, default=50); sp.set_defaults(fn=cmd_requirement_list)
    sp = sub.add_parser("host-event"); sp.add_argument("--repo", default="."); sp.add_argument("--host", default="unknown"); sp.set_defaults(fn=host_event)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        import sys
        from contextlib import redirect_stdout
        from io import StringIO
        arguments = list(sys.argv[1:] if argv is None else argv)
        compact = '--compact-json' in arguments
        arguments = [item for item in arguments if item != '--compact-json']
        args = build_parser().parse_args(arguments)
        # Lifecycle observation and repair/inspection remain usable while the
        # installed host resources await reload. Work advancement must wait.
        # Dispatch by handler: several existing subcommands also use the
        # argparse destination `command` for a shell command or a list of them.
        repair_or_read = args.fn in {
            cmd_init, cmd_bootstrap, cmd_host, host_event, cmd_language_show,
            cmd_language_set, cmd_language_apply, cmd_instructions_update,
            cmd_list, cmd_status, cmd_next, cmd_context, cmd_sessions_list,
            cmd_sessions_show, cmd_decision_list, cmd_handoff, cmd_dashboard, cmd_runtime,
            cmd_sdd_list, cmd_sdd_install, cmd_sdd_select,
        }
        repair_or_read = repair_or_read or getattr(args, 'conformance_action', None) in {'show', 'check'} or getattr(args, 'knowledge_action', None) in {'list', 'show'}
        if not repair_or_read:
            host = getattr(args, "host", None)
            restart = restart_state(repo_root(args.repo), host if host in HOST_ADAPTER_META else detect_current_host())
            if not restart["ready"]:
                print(json.dumps({"ok": False, "error": restart["reason"], "restart": restart,
                                  "next": action("human", restart["next"], reason=restart["reason"])}, ensure_ascii=False, indent=2))
                return 2
        advancing = {cmd_path_assess, cmd_gate, cmd_sdd_workflow, cmd_sdd_route, cmd_sdd_spec, cmd_verify}
        if args.fn in advancing:
            from scripts.cao_work_store import load_index, load_work
            from scripts.project_verification import project_prerequisite
            repo = repo_root(args.repo)
            wid = getattr(args, 'work', None) or load_index(repo).get('active_work')
            work = load_work(repo, wid) if wid else None
            if work and work.get('status') != 'completed':
                prerequisite = project_prerequisite(repo, work)
                if prerequisite:
                    print(json.dumps({'ok': False, 'error': prerequisite['reason'], 'next': prerequisite}))
                    return 2
        if compact:
            from scripts.cli_output import emit_summary
            output = StringIO()
            with redirect_stdout(output):
                code = int(args.fn(args))
            emit_summary(output.getvalue())
            return code
        return int(args.fn(args))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "error": str(exc)}
        if getattr(exc, "diagnostics", None):
            result.update(diagnostic=exc.diagnostics[0], diagnostics=exc.diagnostics)
        code = str(exc).split(':', 1)[0]
        if code.startswith(('PROJECT_CONTEXT', 'PROJECT_SCOPE', 'PROJECT_SOURCE', 'PROJECT_REPOSITORY_SOURCE',
                            'PROJECT_DELEGATION', 'PROJECT_VERSION', 'PROJECT_FIELDS', 'PROJECT_FIELD_CONFLICT',
                            'PROJECT_FIELD_CATEGORY', 'PROJECT_CHANGE', 'PROJECT_BIND', 'PROJECT_REVISION', 'PROJECT_STATE', 'PROJECT_VERIFICATION')):
            from scripts.project_commands import error_context
            result.update(error_context(args, code))
        if str(exc).startswith("SNAPSHOT_"):
            result["reason_code"] = str(exc).split(":", 1)[0]
            result["next"] = "Restore readable repository-owned inputs and executable permissions; replace external business links with repository-owned sources, repair broken/cyclic links or reduce the linked scan scope, then run `cao verify`. Old evidence is not reusable."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
