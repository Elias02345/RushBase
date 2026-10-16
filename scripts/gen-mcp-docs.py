"""Regenerate the tool reference of docs/mcp.md from the shared schemas.

uv run python scripts/gen-mcp-docs.py          # rewrite the section
uv run python scripts/gen-mcp-docs.py --check  # exit 1 when docs/mcp.md is stale
"""

import sys
from pathlib import Path
from typing import Any

from rushbase_core.mcp_tools import MAX_RESULT_CHARS, TOOLS, ToolSpec

DOC = Path(__file__).resolve().parents[1] / "docs" / "mcp.md"
START, END = "<!-- tools:start -->", "<!-- tools:end -->"


def _type(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return str(prop["$ref"]).rsplit("/", 1)[-1]
    if "enum" in prop:
        return " | ".join(f"`{v}`" for v in prop["enum"])
    if "anyOf" in prop:
        options = [_type(p) for p in prop["anyOf"] if p.get("type") != "null"]
        return " or ".join(options) or "any"
    kind = prop.get("type", "any")
    if kind == "array":
        return f"list of {_type(prop.get('items', {}))}"
    return str(kind)


def _rows(tool: ToolSpec) -> list[str]:
    schema = tool.input_schema()
    required = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        note = str(prop.get("description", "")).replace("\n", " ").replace("|", "\\|")
        flag = "yes" if name in required else "no"
        rows.append(f"| `{name}` | {_type(prop)} | {flag} | {note} |")
    return rows


def render() -> str:
    """The generated markdown block (without markers)."""
    lines = [
        f"Results are JSON text of at most {MAX_RESULT_CHARS:,} characters. Lists are paginated: "
        "pass `next_cursor` back as `cursor`. Tools with scope `read` are read-only.",
        "",
        "| Tool | Scope | Writes |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| [`{t.name}`](#{t.name}) | `{t.scope}` | {'yes' if t.writes else 'no'} |" for t in TOOLS
    ]
    for tool in TOOLS:
        lines += ["", f"### {tool.name}", "", tool.description, ""]
        rows = _rows(tool)
        if rows:
            lines += [
                "| Argument | Type | Required | Description |",
                "| --- | --- | --- | --- |",
                *rows,
            ]
        else:
            lines.append("No arguments.")
    return "\n".join(lines)


def main() -> int:
    """Rewrite or check the generated section."""
    text = DOC.read_text()
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    updated = f"{head}{START}\n{render()}\n{END}{tail}"
    if "--check" in sys.argv:
        if updated != text:
            print("docs/mcp.md is stale; run scripts/gen-mcp-docs.py", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
