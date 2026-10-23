"""Bounded category checks, not an exhaustive registry of allowed stacks."""
import re


KNOWN = {
    "framework": {"fastapi", "flask", "django", "springboot", "express", "gin", "react", "vue"},
    "build_tool": {"maven", "mvn", "gradle", "npm", "pnpm", "yarn", "uv", "poetry", "pip", "setuptools"},
    "test_framework": {"pytest", "junit", "junit5", "jest", "vitest", "unittest"},
}


def category_conflicts(values, prefix=""):
    errors = []
    for field, value in values.items():
        token = re.sub(r"[\s_.-]+", "", str(value).casefold())
        known = next((kind for kind, names in KNOWN.items() if token in names), None)
        if known and field != known:
            errors.append({"code": "PROJECT_FIELD_CATEGORY_MISMATCH", "field": prefix + field,
                           "actual": value, "expected_field": prefix + known,
                           "expected": f"{value} is a known {known}; confirm the missing {field} instead of reusing its name"})
    return errors


class TechnologyCategoryError(ValueError):
    def __init__(self, diagnostics):
        super().__init__("PROJECT_FIELD_CATEGORY_MISMATCH")
        self.diagnostics = diagnostics


def validate_categories(values, prefix=""):
    diagnostics = category_conflicts(values, prefix)
    if diagnostics:
        raise TechnologyCategoryError(diagnostics)
