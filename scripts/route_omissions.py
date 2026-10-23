"""Explicit replacements versus native duties inapplicable to BMAD oneshot."""
import re

from scripts.native_route import RouteValidationError, field_error, field_text


def normalize_omissions(raw, artifacts, rules, provider, route, build):
    if not isinstance(raw, list):
        raise field_error("NATIVE_ROUTE_OMISSIONS_INVALID", "$.omitted", "an array", raw)
    if provider == "bmad" and route == "direct-build" and not raw:
        raise field_error("NATIVE_ROUTE_OMISSIONS_REQUIRED", "$.omitted",
                          "explicit replacement duties, or native-rule-backed not_applicable duties for oneshot", raw)
    paths = [a["path"] for a in artifacts]
    normalized, errors = [], []
    for index, item in enumerate(raw):
        field = f"$.omitted[{index}]"
        try:
            if not isinstance(item, dict):
                raise field_error("NATIVE_ROUTE_OMISSIONS_INVALID", field, "an omission object", item)
            name = field_text(item.get("name"), "NATIVE_ROUTE_OMISSION_NAME_REQUIRED", field + ".name")
            reason = field_text(item.get("reason"), "NATIVE_ROUTE_OMISSION_REASON_REQUIRED", field + ".reason")
            disposition = item.get("disposition", "replaced")
            if disposition == "not_applicable":
                source = item.get("native_rule")
                # An unrelated claims/review file cannot justify skipping planning.
                proof = next((r for r in rules if r["path"] == source and
                              re.search(r"\broute\s*:\s*['\"]?oneshot\b", r["quote"])), None)
                if (provider, route, build) != ("bmad", "direct-build", "oneshot") or not proof or item.get("replaced_by"):
                    raise field_error("NATIVE_ROUTE_NOT_APPLICABLE_EVIDENCE_REQUIRED", field + ".native_rule",
                                      "an existing rules[].path whose literal native quote selects route: oneshot; only direct-build/oneshot may declare not_applicable", source)
                normalized.append({"name": name, "reason": reason, "disposition": disposition, "native_rule": source})
                continue
            if disposition != "replaced":
                raise field_error("NATIVE_ROUTE_OMISSIONS_INVALID", field + ".disposition", "replaced or not_applicable", disposition)
            replacement = item.get("replaced_by")
            if not isinstance(replacement, str) or not replacement.strip():
                raise field_error("NATIVE_ROUTE_REPLACEMENT_REQUIRED", field + ".replaced_by",
                                  "a required_artifacts path that actually covers this duty; null is invalid. For a duty absent from native oneshot use disposition: not_applicable with native_rule evidence", replacement, allowed=paths)
            replacement = replacement.strip()
            if replacement not in paths:
                raise field_error("NATIVE_ROUTE_REPLACEMENT_UNBOUND", field + ".replaced_by", "a bound replacement artifact", replacement, allowed=paths)
            normalized.append({"name": name, "reason": reason, "replaced_by": replacement})
        except RouteValidationError as exc:
            errors.extend(exc.diagnostics)
    if errors:
        raise RouteValidationError(errors)
    return normalized
