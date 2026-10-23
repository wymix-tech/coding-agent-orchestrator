"""Strict native intent boundaries shared by route and review fingerprints."""
import re


class IntentStructureError(ValueError):
    def __init__(self, code, issue):
        super().__init__(code)
        self.diagnostics = [{"code": code, "field": "frozen-after-approval", "issue": issue,
                             "expected": "one nonempty, nonnested opening/closing frozen-after-approval pair"}]
        self.diagnostic = self.diagnostics[0]


def frozen_intent(content, *, required=False):
    """Never turn malformed frozen intent into a hash of the entire document.

    Unmarked legacy/non-BMAD documents remain a separate explicit caller case.
    Markers in fenced examples are data, not document structure.
    """
    visible, fence = [], None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is None and marker:
            fence = (marker[1][0], len(marker[1]))
            visible.append(" " * len(line))
        elif fence is not None:
            visible.append(" " * len(line))
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*\n?", line):
                fence = None
        else:
            visible.append(line)
    masked = "".join(visible)
    opens = list(re.finditer(r"<frozen-after-approval\b[^>]*>", masked))
    closes = list(re.finditer(r"</frozen-after-approval\s*>", masked))
    if not opens and not closes:
        if required:
            raise IntentStructureError("NATIVE_INTENT_REQUIRED", "missing_pair")
        return None
    if len(opens) != 1 or len(closes) != 1:
        raise IntentStructureError("NATIVE_INTENT_STRUCTURE_INVALID", "unbalanced_or_multiple_pairs")
    if opens[0].end() > closes[0].start() or opens[0][0].endswith("/>"):
        raise IntentStructureError("NATIVE_INTENT_STRUCTURE_INVALID", "invalid_pair_order")
    value = content[opens[0].end():closes[0].start()]
    if not value.strip():
        raise IntentStructureError("NATIVE_INTENT_EMPTY", "empty_block")
    return value
