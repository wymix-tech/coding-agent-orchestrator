#!/usr/bin/env python3
"""CAO entry point and compatibility exports.

Implementation belongs to the responsibility modules below. New callers should
import those modules directly; this facade preserves existing Python imports
and the source-checkout ``python scripts/cao.py`` entry point.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    _source_root = str(Path(__file__).resolve().parent.parent)
    if _source_root not in sys.path:
        sys.path.insert(0, _source_root)

from scripts.cao_defaults import (
    ACTION_TYPES,
    AUTO_DERIVED_GATES,
    CONFIG_REL,
    DEFAULT_CONFIG,
    EXECUTION_PROFILES,
    HANDOFF_DIR,
    INDEX_REL,
    ORCH,
    PATH_RISK_GATES,
    PATH_RISK_MINIMUM,
    PLACEHOLDER_ENTRYPOINT_COMMANDS,
    PROFILE_BASE_GATES,
    PROFILE_RANK,
    REQUIREMENT_DIR,
    SDD_CATALOG,
    SESSION_DIR,
    SNAPSHOT_IGNORE_DIRS,
    WORK_DIR,
    WORK_STATUSES,
)
from scripts.cao_runtime import (
    action,
    command_exists,
    config,
    deep_merge,
    ensure_initialized,
    load_json,
    locked_command,
    locked_repo,
    matches_any,
    new_id,
    now,
    rel,
    repo_root,
    save_json,
    sha256_bytes,
    sha256_file,
    verification_commands,
    verification_scripts,
)
from scripts.cao_snapshot import (
    SNAPSHOT_FORMAT,
    anchor_verification,
    code_snapshot,
    rules_snapshot,
    verification_snapshot,
    verification_timeout,
)
from scripts.cao_work_store import (
    _activation_denied,
    _append_lifecycle_event,
    _persist_work,
    _read_work,
    _refresh_work,
    drain_events,
    empty_index,
    execution_phase,
    list_lifecycle_events,
    load_index,
    load_work,
    mutate_work,
    other_active_work,
    queue_event,
    recover_index,
    resolve_work,
    save_index,
    save_work,
    stable_work_id,
    sync_index_for_work,
    work_path,
    work_summary,
)
from scripts.cao_requirements import (
    cmd_requirement_list,
    cmd_requirement_next_id,
    new_requirement_path,
    next_requirement_number,
    requirement_evidence,
    requirement_filename,
    requirement_files,
    requirement_skeleton,
    requirement_slug,
    resolve_requirement,
)
from scripts.cao_provider_workflows import (
    PROVIDER_SKILL_ROOTS,
    mistaken_terminal_workflow,
    provider_native_skills,
    provider_output_dirs,
    skill_invocation_guidance,
    validate_workflow_artifacts,
    validate_workflow_entrypoint,
    workflow_command_identities,
    workflow_entrypoint_tokens,
    workflow_skill_identity,
)
from scripts.cao_language import (
    LANGUAGE_LABELS,
    LANGUAGE_POLICY_END,
    LANGUAGE_POLICY_START,
    _openspec_language_config,
    _set_toml_language_values,
    _set_toml_table_values,
    _set_yaml_root_values,
    _strip_managed_yaml_context,
    _toml_quote,
    _update_managed_markdown_section,
    _update_openspec_context,
    apply_installed_sdd_languages,
    apply_provider_language,
    cmd_language_apply,
    cmd_language_set,
    cmd_language_show,
    configure_bmad_language,
    configure_openspec_language,
    configure_speckit_language,
    ensure_language_preference,
    language_label,
    user_language,
)
from scripts.cao_instructions import (
    ALL_INSTRUCTION_TEMPLATES,
    HOST_ADAPTER_META,
    HOST_FRAGMENT_PLACEHOLDERS,
    INSTRUCTION_TEMPLATES_BY_HOST,
    cao_invocation,
    cmd_instructions_update,
    host_adapter_status,
    install_host_adapter,
    install_host_adapters,
    install_project_instruction_templates,
    instruction_template_dir,
    instruction_templates_for_host,
    merge_hook_config,
    package_root_dir,
    render_host_fragment,
    render_project_instruction,
)
from scripts.cao_providers import (
    HOST_SESSION_ENV_KEYS,
    bmad_tool_for_host,
    build_bmad_project_command,
    build_openspec_project_command,
    build_speckit_project_command,
    choose_external_install_command,
    cmd_sdd_install,
    cmd_sdd_list,
    cmd_sdd_select,
    command_output,
    detect_current_host,
    detect_sdd,
    find_python_runtime,
    find_sdd_project_markers,
    host_session_identity,
    infer_sdd_provider,
    install_restart_hosts,
    install_restart_result,
    openspec_tool_for_host,
    run_install_command,
    run_sdd_command,
    sdd_install_instruction,
    sdd_options,
    sdd_prerequisite_check,
    sdd_status,
    select_sdd_provider,
    speckit_integration_for_host,
    version_at_least,
    version_tuple,
)
from scripts.cao_path import (
    build_path_assessment,
    cmd_gate,
    cmd_path_assess,
    cmd_path_show,
    gate_is_auto_derived,
    gate_state,
    normalize_path_risk_flag,
    path_assessment_instruction,
    path_assessment_state,
    path_guidance_for_provider,
)
from scripts.cao_plans import (
    SPRINT_DONE_STATES,
    SPRINT_STATUS_NAMES,
    TASK_LINE_PATTERN,
    _associated_task_roots,
    _checkbox_tasks,
    _pattern_plan_paths,
    _pending_checkbox_tasks,
    _plan_pattern,
    _sprint_selected_states,
    _sprint_states,
    bind_plan_scope,
    bmad_verification_plan_state,
    cmd_sdd_plan_bind,
    observe_plan_scope,
    provider_pending_plan_items,
    provider_verification_plan_state,
)
from scripts.cao_artifacts import (
    artifact_reference,
    cmd_artifact_add,
    cmd_artifact_confirm,
    cmd_artifact_list,
    cmd_artifact_refresh,
    cmd_artifact_remove,
    mark_artifact_confirmed,
    resolve_artifact_path,
)
from scripts.cao_native_sync import (
    _canonical_native_state,
    _native_candidates,
    _native_md_files,
    _read_native_status,
    _slug,
    approval_request_contexts,
    bmad_progress_observation,
    cmd_lifecycle_events,
    cmd_sdd_approval,
    cmd_sdd_artifact_bind,
    cmd_sdd_route,
    cmd_sdd_sync,
    ensure_verification_review,
    expose_approval_context,
    implementation_preflight,
    implementation_write_guard,
    native_route_state,
    native_sync_state,
    observe_implementation_read,
    reconcile_native_sdd_state,
    route_preparation_action,
)
from scripts.cao_completion import (
    completion_next_instruction,
    completion_status,
    sdd_acceptance_current_hashes,
    sdd_acceptance_inputs,
    sdd_acceptance_outputs,
    sdd_acceptance_state,
    sdd_transition,
    verification_inputs,
    work_sdd_ready,
)
from scripts.cao_guidance import (
    agent_guidance,
    current_work_action,
    render_context,
    resume_payload,
    runtime_action_context,
    work_intake_instruction,
    work_prerequisite_action,
)
from scripts.cao_sessions import (
    HANDOFF_IDENTITY_FIELDS,
    _handoff_time_key,
    _read_current_session,
    _session_files,
    choose_resume_work,
    cmd_sessions_list,
    cmd_sessions_show,
    create_handoff,
    end_session,
    find_handoff,
    handoff_history_dir,
    handoff_identity,
    handoff_record_path,
    is_current_session_record,
    latest_handoff,
    list_handoff_records,
    list_sessions,
    load_session,
    register_session,
    save_session,
    session_activity,
    session_path,
    stable_session_id,
    touch_session,
    update_session_event,
)
from scripts.cao_bootstrap import (
    _initialize_state,
    cmd_bootstrap,
    cmd_host,
    cmd_init,
)
from scripts.cao_workflow_commands import (
    cmd_sdd_review,
    cmd_sdd_spec,
    cmd_sdd_workflow,
    record_workflow_unverified,
)
from scripts.cao_verification import (
    _prepare_verification,
    cmd_sdd_acceptance,
    cmd_verification_configure,
    cmd_verification_smoke,
    cmd_verify,
    commit_verification,
    overall_verification_status,
    verification_reason,
    verification_status_for_exit_code,
)
from scripts.cao_work_commands import (
    _completion_blocked,
    _prepare_start,
    cmd_block,
    cmd_checkpoint,
    cmd_context,
    cmd_decision,
    cmd_decision_list,
    cmd_done,
    cmd_handoff,
    cmd_handoff_list,
    cmd_handoff_show,
    cmd_list,
    cmd_next,
    cmd_reopen,
    cmd_resume,
    cmd_start,
    cmd_status,
    cmd_unblock,
)
from scripts.cao_tool_policy import (
    MUTATING_TOOLS,
    MUTATION_PAYLOAD_KEYS,
    SHELL_TOOLS,
    TOOL_NAME_KEYS,
    WRITE_TOOL_PATH_KEYS,
    _collect_path_strings,
    _collect_values_for_keys,
    _event_key,
    _readonly_checksum_tokens,
    _readonly_tokens,
    _shell_segments,
    diagnostic_shell_write,
    git_output_targets,
    intake_writable_dirs,
    native_intake_shell,
    native_output_dirs,
    protected_state_advice,
    readonly_shell,
    sed_effect,
    shell_write_operands,
    tool_cwd,
    tool_effect,
    tool_is_mutating,
    tool_write_targets,
    verification_baseline_state,
    verification_capability_state,
    verification_scaffold_write,
    write_targets_native_sdd_output,
    write_targets_protected_state,
)
from scripts.cao_host_events import (
    host_event,
    native_hook_output,
)
from scripts.cao_cli import (
    build_parser,
    cmd_dashboard,
    main,
)
from typing import Any
from scripts.skill_guidance import CONTRACT_FILES
from typing import Callable
from scripts.verification_setup import SCAFFOLD_MANIFESTS
from scripts.verification_setup import SCAFFOLD_ROOTS
from scripts.startup_request import ORDER as STARTUP_ORDER
from scripts.sdd_acceptance_policy import digest as acceptance_digest
from scripts.completion_policy import acceptance_hash
from scripts.sdd_acceptance_runtime import acceptance_status
from scripts.skill_guidance import action_files
from scripts.native_obligations import active_layers
from scripts.completion_policy import anchor_acceptance
from scripts.native_route import approve_baseline_response
from scripts.native_route import approve_observed_response
import argparse
from scripts.state_store import atomic_json
from scripts.review_binding import attach_reviewer
from scripts.state_store import backup_once
from scripts.native_route import bind_approval_question
from scripts.native_route import bind_request_context
from scripts.light_spec_policy import binding_digest
from scripts.provider_preparation import bmad_preparation
from scripts.completion_policy import verification_fresh as bound_verification_fresh
from scripts.completion_policy import capability_satisfied
from scripts.startup_request import capture_request
from scripts.native_obligations import classify_review
from scripts.verification_setup import configured_scripts
from scripts.startup_request import consume_request
from scripts.verification_setup import declared_scripts
from scripts.native_progress import direct_build_decision
from scripts.native_obligations import dispatch_patch_state
from scripts.native_obligations import dispatch as dispatch_review
from scripts.native_obligations import dispatch_review_next
from scripts.completion_policy import evidence_matches
import fnmatch
import glob
import hashlib
from scripts.native_obligations import implementation_contract
from scripts.native_obligations import implementation_state
from scripts.path_guard import inside
from scripts.native_route import intent_hash
import json
from scripts.light_spec_policy import light_spec_state
from scripts.native_obligations import load_findings
from scripts.light_spec_policy import make_binding
from scripts.native_progress import merge_plan_states
from scripts.native_route import build_route as native_build_route
from scripts.native_obligations import commit_state as native_commit_state
from scripts.native_obligations import digest as native_digest
from scripts.native_route import owned_file as native_owned_file
from scripts.native_obligations import review_state as native_review_state
from scripts.native_route import normalize_decision as normalize_route_decision
from scripts.completion_policy import normalize_scripts
from scripts.completion_policy import normalize_timeout
from scripts.provider_restart import observe_start
import os
from scripts.sdd_acceptance_files import output_paths
from scripts.path_guard import patch_targets
from scripts.startup_request import pending_request
from scripts.native_obligations import planning_state
from scripts.verification_setup import prepare_config
from scripts.path_guard import provider_artifact
import re
from scripts.route_inputs import read_declaration as read_route_declaration
from scripts.sdd_acceptance_runtime import reanchor_record
from scripts.openspec_archive import relocate_acceptance_sources
from scripts.openspec_archive import relocate as relocate_openspec_archive
from scripts.path_guard import repo_file
from scripts.state_store import repo_transaction
from scripts.sdd_acceptance_files import report_hashes
from scripts.startup_request import request_context
from scripts.provider_restart import require_restart
from scripts.completion_policy import required_verification_status
from scripts.completion_policy import reset_requirement
from scripts.native_obligations import resolve_review
from scripts.skill_guidance import guidance as resolve_skill_guidance
from scripts.provider_restart import restart_state
from scripts.native_obligations import review_inputs
from scripts.route_inputs import declaration_path as route_declaration_path
from scripts.native_route import route_state
from scripts.verification_runner import run_script
from scripts.smoke_runner import run_smoke
from scripts.review_binding import runtime_model_name
from scripts.verification_setup import scaffold_instruction
from scripts.verification_setup import setup_instruction
from scripts.verification_setup import setup_state
import shlex
import shutil
from scripts.sdd_acceptance_files import source_hashes
from scripts.native_progress import spec_task_state
import stat
from scripts.native_obligations import submit_review
import subprocess
import sysconfig
from scripts.technology_guidance import technology_instruction
import tempfile
from scripts.completion_policy import tests_satisfied
import time
from scripts.instruction_updates import update_instructions
import uuid
from scripts.verification_setup import verification_setup_context
from scripts.completion_policy import workflow_ready
from functools import wraps
from scripts.route_inputs import writable_input


if __name__ == "__main__":
    sys.exit(main())
