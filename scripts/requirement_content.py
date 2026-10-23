"""Recognize unfilled CAO templates and explicit sensitive requirement text."""
import re


def template_diagnostics(path):
    content = path.read_text(encoding="utf-8-sig")
    visible = re.sub(r"<!--[\s\S]*?-->", "", content)
    # Only CAO-style skeletons qualify. A user's short, differently formatted
    # requirement is not required to adopt CAO's document schema.
    if not re.search(r"(?m)^- (?:需求编号|Requirement ID):", visible):
        return []
    match = re.search(r"(?m)^## (?:需求描述|Description)\s*\n([\s\S]*?)(?=^## |\Z)", visible)
    if match is None or match[1].strip() not in {"", "TODO", "TBD", "待填写", "待补充"}:
        return []
    return [{"code": "REQUIREMENT_CONTENT_REQUIRED", "field": "requirement.description", "path": str(path),
             "expected": "fill the existing requirement description with the actual user request; comments, title and metadata alone are not requirement content"}]


def security_evidence(path):
    content = re.sub(r"<!--[\s\S]*?-->", lambda m: "\n" * m[0].count("\n"), path.read_text(encoding="utf-8-sig"))
    pattern = re.compile(r"登录系统|login\s+system|身份验证|用户认证|password\s*(?:hash|reset|storage)|\bJWT\b|访问令牌|密码(?:存储|哈希|重置)|authentication\s+(?:system|service|flow)|(?:实现|开发|增加|支持).{0,12}(?:登录|认证)|(?:implement|add|build|develop).{0,20}(?:authentication|login)", re.I)
    hits = []
    for number, line in enumerate(content.splitlines(), 1):
        if line.lstrip().startswith(("#", "- 需求编号:", "- 创建日期:", "- Requirement ID:", "- Created:")):
            continue
        if re.search(r"不涉及|不修改|不实现|无需|\bwithout\b|\bdo not\b|\bnot changing\b|\bout of scope\b", line, re.I):
            continue
        if pattern.search(line):
            hits.append({"line": number, "excerpt": line.strip()[:240]})
    return hits
