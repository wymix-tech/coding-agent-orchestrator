"""Work-bound native route decisions and host-observed specification approvals.

CAO's quality profile and a provider's route are separate contracts. Route
records never alter profiles/gates or certify workflow completion. Host input is
an evidence boundary, not a security sandbox against a forged hook invocation.
"""
import hashlib
import json
import re

from scripts.path_guard import repo_file, provider_artifact


class RouteValidationError(ValueError):
    """Keep legacy error codes while identifying invalid preparation/evidence fields."""
    def __init__(self, diagnostics):
        super().__init__(diagnostics[0]["code"])
        self.diagnostics = diagnostics
        self.diagnostic = diagnostics[0]


def rule_text(content):
    """Match the existing UTF-8-sig/universal-newline reader on every platform."""
    return content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def normalize_rules(repo, provider, rules, skills):
    if not isinstance(rules, list) or not rules:
        raise RouteValidationError([{"code": "NATIVE_ROUTE_RULES_REQUIRED", "field": "$.rules",
                                     "expected": "a nonempty array of actual native rule evidence"}])
    normalized, errors = [], []
    for index, rule in enumerate(rules):
        field = f"$.rules[{index}]"
        if not isinstance(rule, dict):
            errors.append({"code": "NATIVE_ROUTE_RULE_INVALID", "field": field, "expected": "an object with path, sha256 and quote"})
            continue
        try:
            path = rule_source(repo, rule.get("path"), provider, skills)
            content = path.read_bytes()
            source = rule_text(content)
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            detail = getattr(exc, "diagnostic", None) or {
                "code": str(exc) if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError) else "NATIVE_ROUTE_RULE_SOURCE_INVALID",
                "path": rule.get("path"), "issue": "source_unreadable",
                "expected": "an existing readable installed native rule file inside this repository"}
            errors.append({**detail, "field": field + ".path"})
            continue
        relative = path.relative_to(repo.resolve()).as_posix()
        sha = hashlib.sha256(content).hexdigest()
        identity = {"code": "NATIVE_ROUTE_RULE_EVIDENCE_MISMATCH", "path": relative}
        if rule.get("sha256") != sha:
            errors.append({**identity, "field": field + ".sha256", "expected": sha,
                           "actual": str(rule.get("sha256"))[:160]})
        try:
            quote = text(rule.get("quote"), "NATIVE_ROUTE_RULE_QUOTE_REQUIRED")
        except ValueError:
            errors.append({**identity, "code": "NATIVE_ROUTE_RULE_QUOTE_REQUIRED", "field": field + ".quote",
                           "expected": "a nonempty verbatim contiguous excerpt"})
            continue
        if quote not in source:
            errors.append({**identity, "field": field + ".quote", "sha256_matches": rule.get("sha256") == sha,
                           "expected": "a verbatim contiguous excerpt, preserving Markdown, paths and internal line breaks; no summaries or ellipses"})
        normalized.append({"path": relative, "sha256": sha, "quote": quote})
    if errors:
        raise RouteValidationError(errors)
    return normalized


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value, error):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value.strip()


def field_error(code, field, expected, actual=None, **details):
    return RouteValidationError([{"code": code, "field": field, "actual": actual,
                                  "expected": expected, **details}])


def field_text(value, code, field):
    if not isinstance(value, str) or not value.strip():
        raise field_error(code, field, "a nonempty string", value)
    return value.strip()


def owned_file(repo, value, dirs):
    # Reject linked source trees as well as escaped files.
    safe = [d for d in dirs if (repo / d).resolve() == repo.resolve() / d]
    path = provider_artifact(repo, text(value, "NATIVE_SOURCE_REQUIRED"), safe)
    if not path.is_file():
        raise ValueError("NATIVE_SOURCE_MISSING")
    return path


def intent_hash(path):
    """BMAD locks intent, not mutable status, task checkboxes or review notes."""
    content = path.read_text(encoding="utf-8-sig")
    blocks = re.findall(r"<frozen-after-approval\b[^>]*>(.*?)</frozen-after-approval>",
                        content, re.S)
    if len(blocks) > 1:
        raise ValueError("NATIVE_INTENT_AMBIGUOUS")
    if blocks:
        if not blocks[0].strip():
            raise ValueError("NATIVE_INTENT_EMPTY")
        return digest(blocks[0])
    # Native status/checkbox progress is mutable; changing task text is not.
    content = re.sub(r"\A---\s*\n(.*?)\n---(?:\n|$)",
                     lambda m: "---\n" + re.sub(r"(?m)^status\s*:.*\n?", "", m[1]) + "\n---\n",
                     content, count=1, flags=re.S)
    content = re.sub(r"(?m)^(\s*[-*+]\s+)\[[ xX]\]", r"\1[ ]", content)
    return digest(content)


def build_route(path):
    content = path.read_text(encoding="utf-8-sig")
    front = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", content, re.S)
    if not front:
        return None
    entries = re.findall(r"(?m)^route\s*:\s*['\"]?([a-z-]+)['\"]?\s*(?:#.*)?$", front[1])
    return entries[0] if len(entries) == 1 else None


def open_questions(path):
    content = path.read_text(encoding="utf-8-sig")
    sections = re.findall(r"(?im)^##[ \t]+(?:Open Questions|开放问题|待确认问题)[ \t]*\n([\s\S]*?)(?=^##[ \t]+|\Z)", content)
    return [body for section in sections
            if (body := re.sub(r"<!--[\s\S]*?-->", "", section).strip())]


def rule_source(repo, value, provider, skills):
    path = repo_file(repo, value)
    repo = repo.resolve()
    roots = {"bmad": ["_bmad"], "openspec": ["openspec"],
             "speckit": [".specify"]}.get(provider, [])
    allowed_roots = [repo / root for root in roots if (repo / root).resolve() == repo / root]
    for skill in skills:
        marker = repo / skill["rel_path"]
        root = marker.parent
        # Native instructions include step files, templates and references,
        # not just SKILL.md. Do not follow a linked skill into product/state.
        if (root.is_relative_to(repo) and root.resolve() == root and
                marker.is_file() and not marker.is_symlink()):
            allowed_roots.append(root)
    issue = None
    if not any(path.is_relative_to(root) for root in allowed_roots):
        issue = "not_installed_native_rule"
    elif not path.is_file():
        issue = "source_missing"
    if issue:
        raise RouteValidationError([{
            "code": "NATIVE_ROUTE_RULE_SOURCE_INVALID", "field": "source", "path": value,
            "issue": issue, "allowed_roots": [root.relative_to(repo).as_posix() for root in allowed_roots],
            "expected": "an existing installed provider rule under an allowed root; use workflow step instructions or references, not a generated SPEC or Agent-created business rules",
        }])
    return path


def spec_preparation_diagnostics(repo, work, dirs):
    """Explain the existing preparation predicate without approving a SPEC."""
    provider = (work.get("sdd") or {}).get("provider")
    ref = (work.get("sdd") or {}).get("native_ref")
    diagnostic = {"code": "NATIVE_SPEC_PREPARATION_REQUIRED", "field": "sdd.native_ref", "path": ref}
    if not ref:
        return [{**diagnostic, "issue": "source_unbound", "expected": "bind the actual native planning output with `cao sdd artifact bind --artifact <path>`"}]
    try:
        path = owned_file(repo, ref, dirs)
        if provider != "bmad":
            return []
        content = path.read_text(encoding="utf-8-sig")
        front = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", content, re.S)
        if not front:
            return [{**diagnostic, "field": "frontmatter", "issue": "frontmatter_missing",
                     "expected": "the installed Build SPEC template's YAML frontmatter, including route: dispatch or route: oneshot; body labels do not count"}]
        if build_route(path) not in {"dispatch", "oneshot"}:
            return [{**diagnostic, "field": "frontmatter.route", "issue": "build_route_invalid",
                     "expected": "exactly one YAML route: dispatch or route: oneshot, chosen by the installed Build workflow"}]
    except (ValueError, OSError, RuntimeError) as exc:
        return [{**diagnostic, "issue": "source_unavailable", "detail": str(exc),
                 "expected": "a readable repository-owned native planning output for this provider"}]
    return []


def normalize_decision(repo, work, raw, dirs, skills):
    if not isinstance(raw, dict):
        raise field_error("NATIVE_ROUTE_DECLARATION_INVALID", "$", "a declaration object", raw)
    sdd = work.get("sdd") or {}
    provider = sdd.get("provider")
    assessment = work.get("path_assessment") or {}
    requirement = work["requirement"]["sha256"]
    for key, expected in (("provider", provider), ("requirement_sha256", requirement)):
        if raw.get(key) != expected:
            raise field_error("NATIVE_ROUTE_IDENTITY_MISMATCH", "$." + key, expected, raw.get(key))
    if file_hash(repo / work["requirement"]["path"]) != requirement:
        raise field_error("NATIVE_ROUTE_REQUIREMENT_STALE", "$.requirement_sha256", "review and reanchor the changed requirement before declaring a route", raw.get("requirement_sha256"))
    if raw.get("profile", assessment.get("profile")) != assessment.get("profile"):
        raise field_error("NATIVE_ROUTE_PROFILE_CHANGE_REQUIRES_ASSESSMENT", "$.profile", assessment.get("profile"), raw.get("profile"))
    workflow = sdd.get("workflow") or {}
    if not workflow and provider == "bmad" and assessment.get("profile") == "light":
        workflow = {"name": "bmad-build", "entrypoint": "invoke skill bmad-build"}
    if not workflow.get("name") or raw.get("workflow") != workflow.get("name"):
        raise field_error("NATIVE_ROUTE_WORKFLOW_MISMATCH", "$.workflow", workflow.get("name") or "select the actual installed workflow first", raw.get("workflow"))
    route = field_text(raw.get("route"), "NATIVE_ROUTE_NAME_REQUIRED", "$.route")
    build = raw.get("build_route")
    if provider == "bmad" and (route not in {"direct-build", "full-planning"}
                               or build not in {"dispatch", "oneshot"}):
        key = "route" if route not in {"direct-build", "full-planning"} else "build_route"
        allowed = ["direct-build", "full-planning"] if key == "route" else ["dispatch", "oneshot"]
        raise field_error("NATIVE_ROUTE_NAME_INVALID", "$." + key, "an installed native route value", raw.get(key), allowed=allowed)
    normalized = normalize_rules(repo, provider, raw.get("rules"), skills)
    required = raw.get("required_artifacts")
    if not isinstance(required, list) or not required:
        raise field_error("NATIVE_ROUTE_ARTIFACTS_REQUIRED", "$.required_artifacts", "a nonempty array of real provider-owned {path, role} artifacts including the primary", required)
    artifacts = []
    for index, item in enumerate(required):
        field = f"$.required_artifacts[{index}]"
        if not isinstance(item, dict):
            raise field_error("NATIVE_ROUTE_ARTIFACT_INVALID", field, "an object with path and role", item)
        try:
            path = owned_file(repo, item.get("path"), dirs)
        except (ValueError, OSError, TypeError) as exc:
            raise field_error(str(exc), field + ".path", "an existing repository-owned native artifact", item.get("path")) from exc
        role = field_text(item.get("role"), "NATIVE_ROUTE_ARTIFACT_ROLE_REQUIRED", field + ".role")
        if any(a["path"] == path.relative_to(repo).as_posix() for a in artifacts):
            raise field_error("NATIVE_ROUTE_DUPLICATE_ARTIFACT", field + ".path", "a unique artifact path", item.get("path"))
        if role == "sprint" and path.name not in {"sprint-status.yaml", "sprint-status.yml"}:
            raise field_error("NATIVE_ROUTE_SPRINT_SOURCE_INVALID", field + ".path", "native sprint-status.yaml or sprint-status.yml", item.get("path"))
        artifacts.append({"path": path.relative_to(repo).as_posix(), "role": role,
                          "intent_sha256": intent_hash(path)})
    primary = field_text(raw.get("primary_artifact"), "NATIVE_ROUTE_PRIMARY_REQUIRED", "$.primary_artifact")
    try:
        primary = owned_file(repo, primary, dirs).relative_to(repo).as_posix()
    except (ValueError, OSError) as exc:
        raise field_error(str(exc), "$.primary_artifact", "an existing provider-owned primary output", primary) from exc
    if primary not in {a["path"] for a in artifacts}:
        raise field_error("NATIVE_ROUTE_PRIMARY_NOT_REQUIRED", "$.primary_artifact", "a path also listed in required_artifacts", primary, allowed=[a["path"] for a in artifacts])
    if provider == "bmad":
        if build_route(repo / primary) != build:
            raise field_error("NATIVE_ROUTE_SPEC_ROUTE_MISMATCH", "$.build_route", build_route(repo / primary), build)
        if route == "full-planning" and not {"prd", "architecture", "epics", "sprint"}.issubset(
                {a["role"] for a in artifacts}):
            raise field_error("NATIVE_ROUTE_FULL_PLANNING_ARTIFACTS_REQUIRED", "$.required_artifacts", "real native prd, architecture, epics and sprint roles for full-planning", [a["role"] for a in artifacts])
    from scripts.route_omissions import normalize_omissions
    omissions = normalize_omissions(raw.get("omitted", []), artifacts, normalized, provider, route, build)
    return {"provider": provider, "requirement_sha256": requirement,
            "assessment_revision": assessment.get("revision"), "profile": assessment.get("profile"),
            "required_gates": assessment.get("required_gates", []),
            "workflow": workflow["name"], "entrypoint": workflow["entrypoint"],
            "route": route, "build_route": build,
            "reason": field_text(raw.get("reason"), "NATIVE_ROUTE_REASON_REQUIRED", "$.reason"),
            "provider_version": field_text(raw.get("provider_version"), "NATIVE_ROUTE_VERSION_REQUIRED", "$.provider_version"),
            "rules": normalized, "required_artifacts": artifacts,
            "primary_artifact": primary, "omitted": omissions,
            "approval_required": provider == "bmad" and build == "dispatch"}


def route_state(repo, work, dirs, required=False):
    sdd = work.get("sdd") or {}
    record = sdd.get("route_decision")
    def blocked(reason, next_step=None):
        return {"ready": False, "reason": reason, "next": next_step or
                "Read installed native routing rules and submit current evidence with `cao sdd route propose --declaration <route.json>`; do not restart intake."}
    if not record:
        return blocked("NATIVE_ROUTE_REQUIRED") if required else {"ready": True, "state": "not_applicable"}
    assessment = work.get("path_assessment") or {}
    workflow = sdd.get("workflow") or {}
    if not workflow and sdd.get("provider") == "bmad" and assessment.get("profile") == "light":
        workflow = {"name": "bmad-build", "entrypoint": "invoke skill bmad-build"}
    if (record.get("requirement_sha256") != work["requirement"]["sha256"]
            or record.get("assessment_revision") != assessment.get("revision")
            or record.get("profile") != assessment.get("profile")
            or record.get("required_gates") != assessment.get("required_gates", [])
            or record.get("provider") != sdd.get("provider")
            or record.get("workflow") != workflow.get("name")
            or record.get("entrypoint") != workflow.get("entrypoint")):
        return blocked("NATIVE_ROUTE_STALE")
    try:
        if file_hash(repo / work["requirement"]["path"]) != record["requirement_sha256"]:
            return blocked("NATIVE_ROUTE_STALE")
        for rule in record["rules"]:
            if file_hash(repo_file(repo, rule["path"])) != rule["sha256"]:
                return blocked("NATIVE_ROUTE_RULES_CHANGED")
        for artifact in record["required_artifacts"]:
            path = owned_file(repo, artifact["path"], dirs)
            if artifact["role"] != "sprint" and intent_hash(path) != artifact["intent_sha256"]:
                return blocked("NATIVE_ROUTE_INTENT_CHANGED")
        if sdd.get("native_ref") != record["primary_artifact"]:
            return blocked("NATIVE_ROUTE_SOURCE_CHANGED")
        primary = owned_file(repo, record["primary_artifact"], dirs)
        if record["provider"] == "bmad" and build_route(primary) != record["build_route"]:
            return blocked("NATIVE_ROUTE_SPEC_ROUTE_MISMATCH")
        questions = open_questions(primary) if record["provider"] == "bmad" else []
        if questions:
            return blocked("NATIVE_SPEC_CLARIFICATION_REQUIRED", "Follow the native planning step: ask the user to resolve the SPEC's Open Questions before requesting approval. Preserve the draft and current Work; incorporate answers and refresh affected route evidence, then request SPEC approval.")
        if record["provider"] == "bmad" and record["route"] == "full-planning":
            plans = (sdd.get("plan_scope") or {}).get("plans", [])
            required_plans = {a["path"] for a in record["required_artifacts"] if a["role"] == "sprint"}
            if not required_plans.issubset({p.get("path") for p in plans if p.get("item_ids")}):
                return blocked("NATIVE_ROUTE_PLAN_SCOPE_REQUIRED", "Bind ALL current requirement stories/epics with `cao sdd plan bind --plan-item <sprint-status.yaml>#<id>`; do not adopt the whole shared project plan.")
        if record["approval_required"]:
            approval = sdd.get("spec_approval") or {}
            if (approval.get("status") != "approved" or approval.get("work_item_id") != work["id"]
                    or approval.get("path") != record["primary_artifact"]
                    or approval.get("requirement_sha256") != record["requirement_sha256"]
                    or approval.get("route_decision_id") != record["id"]
                    or approval.get("intent_sha256") != intent_hash(primary)):
                return blocked("NATIVE_SPEC_APPROVAL_REQUIRED", "Present the spec summary and request approval with `cao sdd approval request`; wait for the user's standalone approval message (批准并继续 / 批准并停止, or Approve and continue / Approve and stop) without appended explanations, then run `cao sdd sync`.")
            if approval.get("stop_after_approval"):
                return blocked("NATIVE_IMPLEMENTATION_PAUSED", "The user approved and stopped. Wait for their explicit instruction to continue implementation; preserve the approved spec.")
    except (ValueError, OSError, KeyError, TypeError, RuntimeError):
        return blocked("NATIVE_ROUTE_SOURCE_UNAVAILABLE")
    return {"ready": True, "state": "accepted", "route": record["route"],
            "build_route": record["build_route"], "decision_id": record["id"]}


def request_context_matches(record, work_id, host, session_id, raw=None, event=None):
    """A reply belongs to a request shown in this Work's host session."""
    if record.get("work_item_id") != work_id or not session_id:
        return False
    if {"host": host, "session_id": session_id} not in record.get("contexts", []):
        return False
    if event == "post_tool":
        call_id = (raw or {}).get("toolCallId") or (raw or {}).get("tool_call_id") or (raw or {}).get("tool_use_id")
        binding = {"host": host, "session_id": session_id, "tool_call_id": call_id,
                   "approval_id": record.get("id")}
        return bool(call_id and binding in record.get("questions", []))
    return True


def bind_request_context(record, work_id, host, session_id):
    if record.get("work_item_id") != work_id or not session_id:
        return False
    context = {"host": host, "session_id": session_id}
    contexts = record.setdefault("contexts", [])
    if context in contexts:
        return False
    contexts.append(context)
    return True


def bind_approval_question(record, work_id, raw, host, session_id):
    """Capture the native tool-call identity before the question is answered."""
    if ((raw.get("tool_name") or raw.get("toolName")) != "ask_user_question"
            or not request_context_matches(record, work_id, host, session_id)):
        return False
    call_id = raw.get("toolCallId") or raw.get("tool_call_id") or raw.get("tool_use_id")
    if not isinstance(call_id, str) or not call_id:
        return False
    binding = {"host": host, "session_id": session_id, "tool_call_id": call_id,
               "approval_id": record["id"]}
    questions = record.setdefault("questions", [])
    if binding in questions:
        return False
    questions.append(binding)
    record["questions"] = questions[-32:]
    return True


def observed_replies(record, work_id, raw, event, host, session_id):
    """Extract replies only from the session/request or its bound question result."""
    if not request_context_matches(record, work_id, host, session_id, raw, event):
        return []
    replies = []
    if event == "prompt_submit":
        value = raw.get("prompt")
        if isinstance(value, str):
            replies.append(value)
    elif event == "post_tool" and (raw.get("tool_name") or raw.get("toolName")) == "ask_user_question" and not raw.get("isError"):
        response = raw.get("tool_response")
        details = raw.get("details") or (response.get("details") if isinstance(response, dict) else {})
        if isinstance(details, dict):
            for answer in details.get("answers", []):
                if isinstance(answer, dict):
                    value = answer.get("answer") or answer.get("selectedLabel")
                    if isinstance(value, str):
                        replies.append(value)
                    for value in answer.get("selectedOptions", []):
                        if isinstance(value, str): replies.append(value)
                        elif isinstance(value, dict) and isinstance(value.get("label"), str): replies.append(value["label"])
    return replies


def approval_action(value, event):
    # UI badges are presentation. Normalize only request-bound tool choices,
    # keeping the original response and its hash in the approval evidence.
    if event == 'post_tool':
        value = re.sub(r'\s*(?:\(Recommended\)|（推荐）|\(推荐\))\s*$', '', value, flags=re.I)
    return value.strip()


def approve_observed_response(repo, work, raw, event, host, session_id, dirs, timestamp):
    """Accept only an explicit approval response after a Work-specific request."""
    approval = (work.get("sdd") or {}).get("spec_approval") or {}
    if approval.get("status") not in {"pending", "approved"}:
        return None
    replies = observed_replies(approval, work['id'], raw, event, host, session_id)
    if event == 'prompt_submit' and (work.get('verification_review') or {}).get('status') == 'pending':
        replies = [reply for reply in replies if not re.fullmatch(r'(?:批准|同意|approve)[。.!！\s]*', reply.strip(), re.I)]
    pattern = r"(?:批准(?:规格|并继续|并停止)?|同意(?:此|该|这份)?规格(?:并继续|并停止)?|approve(?: the)? spec(?:ification)?|approve and (?:continue|stop))"
    if approval.get("status") == "approved":
        if not approval.get("stop_after_approval"):
            return None
        pattern = r"(?:继续实现|开始实现|批准并继续|continue implementation|approve and continue)"
    reply = next((v for v in replies if re.fullmatch(pattern + r"[。.!！\s]*", approval_action(v, event), re.I)), None)
    if reply is None:
        return None
    try:
        path = owned_file(repo, approval["path"], dirs)
        if (approval["intent_sha256"] != intent_hash(path)
                or approval["requirement_sha256"] != work["requirement"]["sha256"]
                or file_hash(repo / work["requirement"]["path"]) != approval["requirement_sha256"]
                or approval["route_decision_id"] != (work["sdd"].get("route_decision") or {}).get("id")):
            return None
    except (ValueError, OSError, KeyError, RuntimeError):
        return None
    approval.update(status="approved", approved_at=timestamp, evidence={"host": host,
                    "session_id": session_id, "work_item_id": work["id"], "approval_id": approval["id"],
                    "tool_call_id": raw.get("toolCallId") or raw.get("tool_call_id") or raw.get("tool_use_id"),
                    "event": event, "response": reply,
                    "response_sha256": digest(reply)})
    approval["stop_after_approval"] = bool(re.search(r"停止|\bstop\b", reply, re.I))
    return approval


def approve_baseline_response(work, raw, event, host, session_id, current_hash, timestamp):
    review = work.get("verification_review") or {}
    if review.get('status') != 'pending' or review.get('config_sha256') != current_hash:
        return None
    replies = observed_replies(review, work['id'], raw, event, host, session_id)
    # An unqualified approval is safe only when there is no competing SPEC request.
    competing = ((work.get('sdd') or {}).get('spec_approval') or {}).get('status') == 'pending'
    pattern = r'(?:批准验证基线|approve verification baseline)'
    if not competing:
        pattern = r'(?:批准验证基线|approve verification baseline|批准|同意|approve)'
    reply = next((v for v in replies if re.fullmatch(pattern + r'[。.!！\s]*', approval_action(v, event), re.I)), None)
    if reply is None:
        return None
    review.update(status="approved", approved_at=timestamp, evidence={"host": host,
                  "session_id": session_id, "work_item_id": work["id"], "approval_id": review["id"],
                  "event": event, "response": reply,
                  "tool_call_id": raw.get('toolCallId') or raw.get('tool_call_id') or raw.get('tool_use_id'),
                  "response_sha256": digest(reply)})
    return review
