"""Load form templates (YAML) and evaluate extracted data against their limits."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.config import TEMPLATES_DIR

FIELD_TYPES = {"text", "number", "date", "time"}


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"
    unit: str | None = None
    min: float | None = None
    max: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Template:
    code: str
    name: str
    description: str = ""
    header_fields: list[Field] = field(default_factory=list)
    table_label: str = ""
    columns: list[Field] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "header_fields": [f.to_dict() for f in self.header_fields],
            "table": {"label": self.table_label, "columns": [c.to_dict() for c in self.columns]},
        }


def _parse_fields(items: list[dict], where: str) -> list[Field]:
    fields = []
    seen = set()
    allowed = set(Field.__dataclass_fields__)
    for item in items or []:
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(f"{where}: thuộc tính không hợp lệ {sorted(unknown)} "
                             "(nhãn có dấu phẩy phải đặt trong ngoặc kép)")
        f = Field(**item)
        if f.type not in FIELD_TYPES:
            raise ValueError(f"{where}: trường '{f.key}' có type không hợp lệ: {f.type}")
        if f.key in seen:
            raise ValueError(f"{where}: trùng key '{f.key}'")
        seen.add(f.key)
        fields.append(f)
    return fields


def parse_template(raw: dict, source: str = "") -> Template:
    for required in ("code", "name"):
        if not raw.get(required):
            raise ValueError(f"{source}: thiếu '{required}'")
    table = raw.get("table") or {}
    return Template(
        code=str(raw["code"]),
        name=raw["name"],
        description=(raw.get("description") or "").strip(),
        header_fields=_parse_fields(raw.get("header_fields"), source),
        table_label=table.get("label", ""),
        columns=_parse_fields(table.get("columns"), source),
    )


def load_templates(directory: Path = TEMPLATES_DIR) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.y*ml")):
        tpl = parse_template(yaml.safe_load(path.read_text(encoding="utf-8")), path.name)
        if tpl.code in templates:
            raise ValueError(f"{path.name}: trùng mã form '{tpl.code}'")
        templates[tpl.code] = tpl
    return templates


_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def to_number(value: Any) -> float | None:
    """Parse a handwritten-style number: '50,2' -> 50.2, '50.2 g' -> 50.2."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUMBER_RE.search(str(value))
    return float(m.group().replace(",", ".")) if m else None


def check_field(f: Field, value: Any) -> str | None:
    """Return 'pass' / 'fail' for numeric fields with limits, 'invalid' if unreadable, else None."""
    if f.type != "number" or (f.min is None and f.max is None):
        return None
    if value in (None, ""):
        return None
    num = to_number(value)
    if num is None:
        return "invalid"
    if (f.min is not None and num < f.min) or (f.max is not None and num > f.max):
        return "fail"
    return "pass"


def evaluate(tpl: Template, data: dict) -> dict:
    """Compare data with template limits.

    Returns {"header": {key: status}, "rows": [{key: status}], "overall": "pass"|"fail"|"n/a"}.
    """
    header = data.get("header", {})
    result: dict[str, Any] = {"header": {}, "rows": []}
    statuses = []
    for f in tpl.header_fields:
        s = check_field(f, (header.get(f.key) or {}).get("value"))
        if s:
            result["header"][f.key] = s
            statuses.append(s)
    for row in data.get("rows", []):
        row_status = {}
        for c in tpl.columns:
            s = check_field(c, (row.get(c.key) or {}).get("value"))
            if s:
                row_status[c.key] = s
                statuses.append(s)
        result["rows"].append(row_status)
    if not statuses:
        result["overall"] = "n/a"
    elif any(s in ("fail", "invalid") for s in statuses):
        result["overall"] = "fail"
    else:
        result["overall"] = "pass"
    return result
