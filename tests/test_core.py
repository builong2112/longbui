import io

import pymupdf
import pytest
from PIL import Image

from app import db, extractor
from app.pdf_utils import UnsupportedFile, split_upload
from app.templates_loader import evaluate, load_templates, parse_template, to_number

from .conftest import SAMPLE_WC, cell, make_jpeg


def test_sample_templates_load():
    templates = load_templates()
    assert {"IPC-WC", "IPC-BTP"} <= set(templates)
    wc = templates["IPC-WC"]
    weight = next(c for c in wc.columns if c.key == "net_weight")
    assert (weight.min, weight.max, weight.unit) == (49.0, 51.5, "g")


def test_template_rejects_bad_type():
    with pytest.raises(ValueError):
        parse_template({"code": "X", "name": "X", "header_fields": [{"key": "a", "label": "A", "type": "float"}]})


@pytest.mark.parametrize("raw,expected", [("50,2", 50.2), ("50.2 g", 50.2), ("pH 5", 5.0), ("", None), ("abc", None), (7, 7.0)])
def test_to_number(raw, expected):
    assert to_number(raw) == expected


def test_evaluate_flags_out_of_spec():
    tpl = load_templates()["IPC-WC"]
    result = evaluate(tpl, SAMPLE_WC)
    assert result["rows"][0]["net_weight"] == "pass"
    assert result["rows"][1]["net_weight"] == "fail"  # 48,7 < 49.0
    assert result["overall"] == "fail"


def test_evaluate_unreadable_number_is_invalid():
    tpl = load_templates()["IPC-WC"]
    data = {"header": {}, "rows": [{"net_weight": cell("??")}]}
    assert evaluate(tpl, data)["rows"][0]["net_weight"] == "invalid"


def test_output_schema_matches_template():
    tpl = load_templates()["IPC-BTP"]
    schema = extractor.build_output_schema(tpl)
    assert schema["properties"]["header"]["required"] == [f.key for f in tpl.header_fields]
    row = schema["properties"]["rows"]["items"]
    assert set(row["properties"]) == {c.key for c in tpl.columns}
    assert row["additionalProperties"] is False


def test_split_upload_image_fixes_size():
    big = Image.new("RGB", (4000, 3000), "white")
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    pages = split_upload("photo.PNG", buf.getvalue())
    assert len(pages) == 1
    assert max(Image.open(io.BytesIO(pages[0])).size) == 2000


def test_split_upload_pdf_pages():
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    pages = split_upload("scan.pdf", doc.tobytes())
    assert len(pages) == 3
    assert Image.open(io.BytesIO(pages[0])).format == "JPEG"


def test_split_upload_rejects_unknown():
    with pytest.raises(UnsupportedFile):
        split_upload("notes.docx", b"x")


def test_diff_data_lists_changes():
    new = {
        "header": {**SAMPLE_WC["header"], "batch_no": cell("L240916", "verified")},
        "rows": SAMPLE_WC["rows"][:1],
        "notes": "sửa",
    }
    changes = db.diff_data(SAMPLE_WC, new)
    assert ("header.batch_no", "L240915", "L240916") in changes
    assert ("rows.2", "(xóa dòng)", "") in changes
    assert ("notes", "", "sửa") in changes


def test_user_pin_check():
    db.create_user("qa1", "QA", "4321", role="qa")
    assert db.verify_user("qa1", "4321")["role"] == "qa"
    assert db.verify_user("qa1", "0000") is None
    assert db.verify_user("nobody", "4321") is None


def test_make_jpeg_helper():
    assert make_jpeg()[:2] == b"\xff\xd8"
