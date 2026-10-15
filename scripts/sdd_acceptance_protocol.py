"""Machine-readable request/report schemas and an honest executable template."""
from copy import deepcopy
from pathlib import Path


def protocol():
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    relative = {"type": "string", "pattern": r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*\u0000)(?!.*(?:^|/)\.{1,2}(?:/|$))[^/]+(?:/[^/]+)*$"}
    evidence = {"type": "object", "required": ["path", "reason"],
                "properties": {"path": relative, "reason": text}}
    criterion = {"type": "object", "required": ["id", "source", "kind", "description"],
                 "properties": {"id": text, "source": relative, "kind": {"enum": ["requirement", "design"]}, "description": text}}
    identity = {"run_id": text, "input_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}, "provider": text}
    report_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                     "required": [*identity, "items"], "properties": {**identity, "items": {
                         "type": "array", "minItems": 1, "items": {"type": "object", "required": ["id", "status", "evidence"],
                         "properties": {"id": text, "status": {"enum": ["passed", "failed", "blocked"]},
                                        "evidence": {"type": "array", "minItems": 1, "items": evidence}}}}}}
    declaration = {"type": "object", "required": ["spec", "criteria", "executor"], "properties": {
        "spec": relative, "sources": {"type": "array", "items": relative}, "outputs": {"type": "array", "items": relative},
        "criteria": {"type": "array", "minItems": 1, "items": criterion}, "executor": {
            "type": "object", "required": ["mode", "entrypoint"], "properties": {
                "mode": {"enum": ["command", "skill"]}, "entrypoint": text,
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0}}}}}
    request_schema = {"$schema": report_schema["$schema"], "type": "object", "required": [*identity, "inputs", "declaration"],
                      "properties": {**identity, "inputs": {"type": "object"}, "declaration": declaration}}
    request = {"run_id": "sdda-example", "input_sha256": "0" * 64, "provider": "openspec", "inputs": {}, "declaration": {
        "spec": "openspec/changes/add-demo/spec.md", "sources": ["openspec/changes/add-demo/spec.md"],
        "criteria": [{"id": "AC-1", "source": "openspec/changes/add-demo/spec.md", "kind": "requirement", "description": "Real CLI behavior is correct"}],
        "executor": {"mode": "command", "entrypoint": "python3 scripts/verification/acceptance.py --checks acceptance-checks.json", "timeout_seconds": 300},
        "outputs": ["openspec/cao-acceptance/<work-id>/report.json"]}}
    report = {**{key: request[key] for key in identity}, "items": [{"id": "AC-1", "status": "blocked",
              "evidence": [{"path": request["declaration"]["spec"], "reason": "Example only; configure and execute a real check"}]}]}
    return deepcopy({"environment": {"name": "CAO_SDD_ACCEPTANCE_REQUEST", "encoding": "JSON string, not a file path"},
                     "request_schema": request_schema, "report_schema": report_schema,
                     "request_example": request, "report_example": report,
                     "executor_template": str(Path(__file__).with_name("acceptance_executor.py")),
                     "rules": ["Read request.declaration.criteria", "Return exactly one item per criterion",
                               "evidence must be a nonempty array of path/reason objects", "Write every declared outputs file before printing one JSON report to stdout",
                               "Send command diagnostics to stderr; missing checks must block"]})
