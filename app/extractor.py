"""Read a scanned IPC form image with Claude and return structured JSON.

Output shape (same for every template):
{
  "header": {field_key: {"value": str, "confidence": "high|medium|low"}},
  "rows":   [{column_key: {"value": str, "confidence": ...}}, ...],
  "notes":  str   # anything unreadable / crossed out / unusual
}
"""
from __future__ import annotations

import base64
import json
from typing import Any

import anthropic

from app import config
from app.templates_loader import Template

CONFIDENCE = ["high", "medium", "low"]

SYSTEM_PROMPT = """Bạn là trợ lý số hóa hồ sơ IPC (kiểm soát trong quá trình sản xuất) của nhà máy mỹ phẩm.
Nhiệm vụ: đọc ảnh chụp/scan một biểu mẫu giấy in sẵn, có chữ viết tay tiếng Việt, và chép lại CHÍNH XÁC dữ liệu vào JSON.

Quy tắc:
- Chép đúng như trên giấy, không tự sửa, không suy đoán số liệu. Ô trống -> value = "".
- Số thập phân giữ nguyên cách viết (ví dụ "50,2"). Ngày viết dạng DD/MM/YYYY nếu đọc được đủ.
- Mỗi dòng có dữ liệu trong bảng là một phần tử của "rows", theo thứ tự từ trên xuống. Bỏ qua dòng hoàn toàn trống.
- confidence: "high" nếu đọc rõ, "medium" nếu hơi mờ/khó đọc, "low" nếu gần như đoán.
- Chỗ bị gạch xóa, sửa đè, ký hiệu lạ hoặc thông tin không thuộc trường nào -> ghi vào "notes".
"""


def _cell_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "confidence": {"type": "string", "enum": CONFIDENCE},
        },
        "required": ["value", "confidence"],
        "additionalProperties": False,
    }


def build_output_schema(tpl: Template) -> dict:
    def obj(fields):
        return {
            "type": "object",
            "properties": {f.key: _cell_schema() for f in fields},
            "required": [f.key for f in fields],
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "header": obj(tpl.header_fields),
            "rows": {"type": "array", "items": obj(tpl.columns)},
            "notes": {"type": "string"},
        },
        "required": ["header", "rows", "notes"],
        "additionalProperties": False,
    }


def _describe_fields(fields) -> str:
    lines = []
    for f in fields:
        extra = f", đơn vị {f.unit}" if f.unit else ""
        lines.append(f'- "{f.key}": {f.label} ({f.type}{extra})')
    return "\n".join(lines)


def build_prompt(tpl: Template) -> str:
    return (
        f"Biểu mẫu: {tpl.code} – {tpl.name}\n{tpl.description}\n\n"
        f"Các trường phần đầu (header):\n{_describe_fields(tpl.header_fields)}\n\n"
        f"Các cột của bảng '{tpl.table_label}' (rows):\n{_describe_fields(tpl.columns)}\n\n"
        "Hãy trích xuất dữ liệu từ ảnh theo đúng cấu trúc JSON đã định nghĩa."
    )


class ExtractionError(Exception):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _image_block(image_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        },
    }


def _call(content: list[dict], schema: dict, max_tokens: int) -> str:
    """Send one request; return the JSON text. Raises ExtractionError with a Vietnamese message."""
    try:
        response = _client().beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": config.CLAUDE_EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
            # Nếu model từ chối (hiếm gặp), API tự chạy lại trên model dự phòng.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionError("API key Anthropic không hợp lệ hoặc chưa cấu hình.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Vượt giới hạn gọi API, thử lại sau ít phút.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Lỗi API ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Không kết nối được tới Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("AI từ chối xử lý ảnh này.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("Biểu mẫu quá dài, kết quả bị cắt. Hãy chụp từng phần.")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise ExtractionError("AI không trả về dữ liệu.")
    return text


def extract(tpl: Template, image_bytes: bytes) -> dict[str, Any]:
    text = _call(
        [_image_block(image_bytes), {"type": "text", "text": build_prompt(tpl)}],
        build_output_schema(tpl),
        max_tokens=16000,
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError("AI trả về JSON không hợp lệ.") from e


def classify(templates: dict[str, Template], image_bytes: bytes) -> str:
    """Pick which template an image matches (used when the user selects 'auto-detect')."""
    codes = list(templates)
    listing = "\n".join(f"- {t.code}: {t.name}. {t.description}" for t in templates.values())
    schema = {
        "type": "object",
        "properties": {"form_code": {"type": "string", "enum": codes}},
        "required": ["form_code"],
        "additionalProperties": False,
    }
    text = _call(
        [
            _image_block(image_bytes),
            {"type": "text", "text": f"Ảnh này là biểu mẫu nào trong danh sách sau?\n{listing}"},
        ],
        schema,
        max_tokens=2000,
    )
    return json.loads(text)["form_code"]
