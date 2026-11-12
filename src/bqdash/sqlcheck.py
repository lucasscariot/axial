"""Static checks on saved SQL: single read-only SELECT, parameter usage."""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"""
    (?P<comment>--[^\n]*|\#[^\n]*|/\*.*?\*/)
  | (?P<string>(?:[rRbB]{0,2})(?:'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"))
  | (?P<ident>`[^`]*`)
  | (?P<other>.)
    """,
    re.S | re.X,
)

_PARAM = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


def strip_sql(sql: str) -> str:
    """Remove comments and replace string/backtick literals with placeholders."""
    out: list[str] = []
    for m in _TOKEN.finditer(sql):
        kind = m.lastgroup
        if kind == "comment":
            out.append(" ")
        elif kind == "string":
            out.append("''")
        elif kind == "ident":
            out.append("`x`")
        else:
            out.append(m.group())
    return "".join(out)


def check_read_only_select(sql: str) -> list[str]:
    """Return problems; empty when the SQL is one SELECT/WITH statement."""
    body = strip_sql(sql).strip()
    problems: list[str] = []
    if not body:
        return ["SQL is empty"]
    if body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        problems.append("SQL must be a single statement (found ';')")
    first = re.match(r"[\s(]*([A-Za-z]+)", body)
    if not first or first.group(1).upper() not in ("SELECT", "WITH"):
        problems.append("SQL must start with SELECT or WITH")
    if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|DROP|ALTER|GRANT|REVOKE|DECLARE|EXECUTE|CALL|BEGIN|EXPORT|LOAD)\b\s", body, re.I):
        # Keywords can appear inside CTE names etc.; only flag a statement-like use.
        for m in re.finditer(r"(^|[;)\s])(INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|MERGE\s+\S|TRUNCATE\s+TABLE|CREATE\s|DROP\s|ALTER\s|GRANT\s|REVOKE\s|DECLARE\s|EXECUTE\s+IMMEDIATE|CALL\s|BEGIN\b|EXPORT\s+DATA|LOAD\s+DATA)", body, re.I):
            problems.append(f"SQL contains a non-SELECT statement keyword: {m.group(2).strip()}")
            break
    return problems


def referenced_params(sql: str) -> set[str]:
    return set(_PARAM.findall(strip_sql(sql)))
