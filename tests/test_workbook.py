"""write_workbook / read_workbook: layout, _meta, protection, validations, formula safety."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

import ratersheet
from ratersheet import (
    RatersheetError,
    Scale,
    SheetRow,
    read_workbook,
    text_sha256,
    write_workbook,
)
from conftest import scale_obj

KEY = "ab" * 32
ACC = {"id": "accuracy", "label": "Accuracy", "type": "ordinal", "min": 1, "max": 5, "description": "1 = wrong, 5 = right"}
HARM = {"id": "harm", "label": "Harm level", "type": "nominal", "values": ["none", "minor", "serious"]}
SCALES: list[Scale] = [scale_obj(ACC), scale_obj(HARM)]


def rows(n: int = 4, *, context: bool = False) -> list[SheetRow]:
    return [
        SheetRow(rating_id=f"AAAA-{i:04d}".replace("0", "B"), text=f"Synthetic text {i}", context=f"Context {i}" if context else None)
        for i in range(1, n + 1)
    ]


def write(
    path: Path,
    *,
    rws: list[SheetRow] | None = None,
    scales: list[Scale] | None = None,
    comments: bool = False,
    title: str = "Synthetic study",
    instructions: str = "Please rate carefully.",
    rater_id: str = "R7",
) -> Path:
    write_workbook(
        path,
        title=title,
        instructions=instructions,
        rater_id=rater_id,
        rows=rows() if rws is None else rws,
        scales=SCALES if scales is None else scales,
        comments=comments,
        key_sha256=KEY,
    )
    return path


def headers(path: Path) -> list[str]:
    ws = openpyxl.load_workbook(path)["Ratings"]
    return [c.value for c in ws[1] if c.value is not None]


# ---- layout ---------------------------------------------------------------------------------


def test_sheets_exist_and_meta_is_very_hidden(tmp_path: Path) -> None:
    wb = openpyxl.load_workbook(write(tmp_path / "w.xlsx"))
    assert {"Instructions", "Ratings", "_meta"} <= set(wb.sheetnames)
    assert wb["_meta"].sheet_state == "veryHidden"
    assert wb["Ratings"].sheet_state == "visible" and wb["Instructions"].sheet_state == "visible"


@pytest.mark.parametrize(
    "context,comments,expected",
    [
        (False, False, ["rating_id", "text", "Accuracy", "Harm level"]),
        (True, False, ["rating_id", "context", "text", "Accuracy", "Harm level"]),
        (False, True, ["rating_id", "text", "Accuracy", "Harm level", "comment"]),
        (True, True, ["rating_id", "context", "text", "Accuracy", "Harm level", "comment"]),
    ],
)
def test_column_order(tmp_path: Path, context: bool, comments: bool, expected: list[str]) -> None:
    p = write(tmp_path / "w.xlsx", rws=rows(context=context), comments=comments)
    assert headers(p) == expected


def test_context_column_appears_if_any_row_has_context(tmp_path: Path) -> None:
    rws = rows()
    rws[2] = SheetRow(rating_id=rws[2].rating_id, text=rws[2].text, context="Only this one")
    p = write(tmp_path / "w.xlsx", rws=rws)
    assert "context" in headers(p)
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert ws.cell(4, 2).value == "Only this one"
    assert ws.cell(2, 2).value in (None, "")


def test_rows_are_in_given_order_starting_at_row_two(tmp_path: Path) -> None:
    rws = rows(5)
    ws = openpyxl.load_workbook(write(tmp_path / "w.xlsx", rws=rws))["Ratings"]
    col = headers(tmp_path / "w.xlsx").index("rating_id") + 1
    assert [ws.cell(r, col).value for r in range(2, 7)] == [r.rating_id for r in rws]
    assert ws.cell(7, col).value in (None, "")


def test_rating_cells_start_empty(tmp_path: Path) -> None:
    ws = openpyxl.load_workbook(write(tmp_path / "w.xlsx"))["Ratings"]
    for r in range(2, 6):
        assert ws.cell(r, 3).value in (None, "") and ws.cell(r, 4).value in (None, "")


# ---- _meta ----------------------------------------------------------------------------------


def test_meta_json(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", rws=rows(context=True), comments=True, title="T", rater_id="R7")
    wb = openpyxl.load_workbook(p)
    meta = json.loads(wb["_meta"]["A1"].value)
    assert meta["format_version"] == 1
    assert meta["tool_version"] == ratersheet.__version__
    assert meta["title"] == "T" and meta["rater_id"] == "R7" and meta["key_sha256"] == KEY
    assert meta["columns"] == [
        {"header": "rating_id", "role": "rating_id"},
        {"header": "context", "role": "context"},
        {"header": "text", "role": "text"},
        {"header": "Accuracy", "role": "scale", "scale_id": "accuracy"},
        {"header": "Harm level", "role": "scale", "scale_id": "harm"},
        {"header": "comment", "role": "comment"},
    ]


def test_meta_lists_only_columns_that_exist(tmp_path: Path) -> None:
    meta = json.loads(openpyxl.load_workbook(write(tmp_path / "w.xlsx"))["_meta"]["A1"].value)
    assert [c["role"] for c in meta["columns"]] == ["rating_id", "text", "scale", "scale"]


# ---- Instructions sheet ---------------------------------------------------------------------


def test_instructions_sheet_content(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", title="My Title", instructions="Rate each answer calmly.", rater_id="R7")
    wb = openpyxl.load_workbook(p)
    blob = "\n".join(str(c.value) for row in wb["Instructions"].iter_rows() for c in row if c.value is not None)
    assert "My Title" in blob
    assert "Rater: R7" in blob
    assert "Rate each answer calmly." in blob
    assert f"Key commitment (SHA-256): {KEY}" in blob
    assert f"Generated by ratersheet {ratersheet.__version__}" in blob
    for s in SCALES:
        assert s.label in blob
        for v in s.values:
            assert str(v) in blob
    assert "1 = wrong, 5 = right" in blob


# ---- protection, validations, comfort ---------------------------------------------------------


def test_protection_and_unlocked_cells(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", rws=rows(context=True), comments=True)
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert ws.protection.sheet is True
    assert not ws.protection.password, "protection must not use a password"
    hdr = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
    for r in range(2, 6):
        for name in ("Accuracy", "Harm level", "comment"):
            assert ws.cell(r, hdr[name]).protection.locked is False, f"{name} row {r} must be unlocked"
        for name in ("rating_id", "context", "text"):
            assert ws.cell(r, hdr[name]).protection.locked is True, f"{name} row {r} must be locked"
    for c in hdr.values():
        assert ws.cell(1, c).protection.locked is True


def test_data_validation_per_scale(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", rws=rows(4))
    ws = openpyxl.load_workbook(p)["Ratings"]
    dvs = ws.data_validations.dataValidation
    assert len(dvs) >= 2
    hdr = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
    for scale in SCALES:
        col = hdr[scale.label]
        mine = [d for d in dvs if all(ws.cell(r, col).coordinate in d.sqref for r in range(2, 6))]
        assert len(mine) == 1, f"exactly one validation should cover column {scale.label}"
        d = mine[0]
        assert d.type == "list"
        assert d.allow_blank is True
        for v in scale.values:
            assert str(v) in str(d.formula1), f"{v} missing from the list of {scale.label}"
            assert str(v) in str(d.error), f"{v} missing from the error message of {scale.label}"
        assert ws.cell(1, col).coordinate not in d.sqref, "header cell must not be validated"


def test_validation_does_not_cover_text_columns(tmp_path: Path) -> None:
    ws = openpyxl.load_workbook(write(tmp_path / "w.xlsx", rws=rows(context=True)))["Ratings"]
    for d in ws.data_validations.dataValidation:
        for r in range(2, 6):
            for c in (1, 2, 3):
                assert ws.cell(r, c).coordinate not in d.sqref


def test_comfort_settings(tmp_path: Path) -> None:
    ws = openpyxl.load_workbook(write(tmp_path / "w.xlsx", rws=rows(context=True), comments=True))["Ratings"]
    assert ws.freeze_panes == "A2"
    for c in range(1, ws.max_column + 1):
        assert ws.cell(1, c).font.b is True, f"header column {c} should be bold"
    hdr = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
    assert ws.cell(2, hdr["text"]).alignment.wrap_text is True
    assert ws.cell(2, hdr["context"]).alignment.wrap_text is True
    for name in ("Accuracy", "Harm level"):
        assert ws.cell(2, hdr[name]).fill.fill_type is not None, "rating columns are lightly filled"
    assert ws.cell(2, hdr["text"]).fill.fill_type is None or ws.cell(2, hdr["text"]).fill != ws.cell(2, hdr["Accuracy"]).fill
    letter = openpyxl.utils.get_column_letter(hdr["text"])
    assert ws.column_dimensions[letter].width and ws.column_dimensions[letter].width > 15


# ---- text safety ------------------------------------------------------------------------------


DANGEROUS = ["=1+1", "+cmd|' /C calc'!A0", "-5", "@SUM(A1:A2)", "=HYPERLINK(\"http://example.invalid\")", "  =padded", "TRUE", "123"]


@pytest.mark.parametrize("text", DANGEROUS)
def test_text_context_and_rating_id_stay_strings(tmp_path: Path, text: str) -> None:
    rws = [SheetRow(rating_id="ABCD-2345", text=text, context=text), SheetRow(rating_id="ABCD-2346", text="ok", context="ok")]
    p = write(tmp_path / "w.xlsx", rws=rws)
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert ws["B2"].value == text and ws["B2"].data_type == "s", f"context {text!r}"
    assert ws["C2"].value == text and ws["C2"].data_type == "s", f"text {text!r}"
    assert ws["A2"].data_type == "s"
    back = read_workbook(p)
    assert back.rows[0].text == text and back.rows[0].context == text


def test_rating_id_starting_with_equals_is_still_text(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", rws=[SheetRow(rating_id="=AB", text="x", context=None)])
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert ws["A2"].value == "=AB" and ws["A2"].data_type == "s"


def test_multiline_and_unicode_text_round_trip(tmp_path: Path) -> None:
    t = "Line one\nLine two with é and 日本語 and “quotes”\n\tTabbed"
    p = write(tmp_path / "w.xlsx", rws=[SheetRow(rating_id="ABCD-2345", text=t, context=None)])
    assert read_workbook(p).rows[0].text == t


def test_long_text_round_trip(tmp_path: Path) -> None:
    t = "x" * 32767
    p = write(tmp_path / "w.xlsx", rws=[SheetRow(rating_id="ABCD-2345", text=t, context=None)])
    assert read_workbook(p).rows[0].text == t


# ---- read_workbook ----------------------------------------------------------------------------


def test_read_workbook_basic(tmp_path: Path) -> None:
    rws = rows(3, context=True)
    p = write(tmp_path / "w.xlsx", rws=rws, comments=True, rater_id="R7")
    rb = read_workbook(p)
    assert Path(rb.path) == p
    assert rb.meta["rater_id"] == "R7" and rb.meta["key_sha256"] == KEY
    assert len(rb.rows) == 3
    for i, (src, got) in enumerate(zip(rws, rb.rows)):
        assert got.rating_id == src.rating_id and got.text == src.text and got.context == src.context
        assert got.values == {"accuracy": None, "harm": None}
        assert got.comment is None
        assert got.row_number == i + 2


def test_read_workbook_returns_raw_values_and_comments(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", comments=True)
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    ws["C2"].value = 4
    ws["D2"].value = "Minor"
    ws["E2"].value = "a note"
    ws["C3"].value = 4.0
    ws["C4"].value = " 4 "
    ws["C5"].value = "=2+2"
    wb.save(p)
    rb = read_workbook(p)
    assert rb.rows[0].values == {"accuracy": 4, "harm": "Minor"} and rb.rows[0].comment == "a note"
    assert rb.rows[1].values["accuracy"] == 4
    assert rb.rows[2].values["accuracy"] == " 4 "
    assert rb.rows[3].values["accuracy"] == "=2+2", "formulas are read as formulas, not cached values"


def test_read_workbook_finds_columns_by_header_not_position(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    ws.insert_cols(1)  # shift everything one column to the right
    wb.save(p)
    rb = read_workbook(p)
    assert [r.text for r in rb.rows] == [f"Synthetic text {i}" for i in range(1, 5)]


def test_read_workbook_skips_empty_rows(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    for c in range(1, ws.max_column + 1):
        ws.cell(3, c).value = None
    wb.save(p)
    rb = read_workbook(p)
    assert len(rb.rows) == 3
    assert [r.row_number for r in rb.rows] == [2, 4, 5]


def test_read_workbook_rejects_plain_workbook(tmp_path: Path) -> None:
    p = tmp_path / "plain.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Ratings"
    wb.save(p)
    with pytest.raises(RatersheetError):
        read_workbook(p)


def test_read_workbook_rejects_missing_meta(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    del wb["_meta"]
    wb.save(p)
    with pytest.raises(RatersheetError):
        read_workbook(p)


def test_read_workbook_rejects_unparseable_meta(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    wb["_meta"]["A1"].value = "{not json"
    wb.save(p)
    with pytest.raises(RatersheetError):
        read_workbook(p)


def test_read_workbook_rejects_missing_ratings_sheet(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    del wb["Ratings"]
    wb.save(p)
    with pytest.raises(RatersheetError):
        read_workbook(p)


@pytest.mark.parametrize("header", ["rating_id", "text", "Accuracy", "Harm level"])
def test_read_workbook_rejects_missing_column(tmp_path: Path, header: str) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == header:
            ws.cell(1, c).value = f"{header} (renamed)"
    wb.save(p)
    with pytest.raises(RatersheetError):
        read_workbook(p)


def test_write_workbook_accepts_str_path(tmp_path: Path) -> None:
    p = tmp_path / "w.xlsx"
    write_workbook(str(p), title="t", instructions="", rater_id="R1", rows=rows(), scales=SCALES, comments=False, key_sha256=KEY)
    assert read_workbook(str(p)).rows


def test_empty_instructions_are_fine(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", instructions="")
    assert len(read_workbook(p).rows) == 4


def test_scale_with_ten_values_validation_lists_all(tmp_path: Path) -> None:
    sc = scale_obj({"id": "big", "type": "ordinal", "min": 0, "max": 10})
    ws = openpyxl.load_workbook(write(tmp_path / "w.xlsx", scales=[sc]))["Ratings"]
    d = ws.data_validations.dataValidation[0]
    for v in range(0, 11):
        assert str(v) in str(d.formula1)



# ---- what a rater reads on the Instructions sheet ----------------------------------------------


def instructions_blob(path: Path) -> str:
    ws = openpyxl.load_workbook(path)["Instructions"]
    return "\n".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)


def test_key_note_does_not_call_the_key_sealed(tmp_path: Path) -> None:
    blob = instructions_blob(write(tmp_path / "w.xlsx"))
    assert "sealed" not in blob.lower(), "nothing seals the key file; the sheet must not say so"
    assert "fingerprint of the file that links each text to its source" in blob


def test_instructions_say_what_required_means_without_forbidding_an_empty_cell(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx")
    lines = [line for line in instructions_blob(p).split("\n") if line.startswith("Required")]
    note = [line for line in lines if "study team needs a rating in that column for every row" in line]
    assert len(note) == 1, lines
    assert "must" not in note[0] and "empty" in note[0], "the drop-down message allows an empty cell"
    assert not [line for line in lines if line.startswith("Required = no")], "no scale here is optional"
    dropdown_errors = [dv.error for dv in openpyxl.load_workbook(p)["Ratings"].data_validations.dataValidation]
    assert all("leave the cell empty" in e for e in dropdown_errors)


# ---- number format of the rating cells -----------------------------------------------------------


def test_nominal_rating_cells_are_text_so_values_are_not_retyped(tmp_path: Path) -> None:
    # In a General cell a spreadsheet turns a picked TRUE, 0.5, 1-2 or 50% into a boolean,
    # a number or a date; in a text cell it stays the scale's own spelling.
    credit = scale_obj({"id": "credit", "label": "Credit", "type": "nominal", "values": ["0", "0.5", "1"]})
    p = write(tmp_path / "w.xlsx", scales=[scale_obj(ACC), credit], comments=True)
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert headers(p) == ["rating_id", "text", "Accuracy", "Credit", "comment"]
    for r in range(2, 6):
        assert ws.cell(r, 4).number_format == "@", "nominal cells must have the text format"
        assert ws.cell(r, 4).protection.locked is False and ws.cell(r, 4).value is None
        assert ws.cell(r, 3).number_format == "General", "an ordinal 4 must still arrive as a number"


def test_comment_cells_are_text_so_a_typed_comment_is_not_retyped(tmp_path: Path) -> None:
    # In a General cell a typed 1/2, 007, 50% or true becomes a date, a number or a boolean, and
    # the rater's words are lost before the file comes back.
    p = write(tmp_path / "w.xlsx", comments=True)
    ws = openpyxl.load_workbook(p)["Ratings"]
    assert headers(p) == ["rating_id", "text", "Accuracy", "Harm level", "comment"]
    for r in range(2, 6):
        assert ws.cell(r, 5).number_format == "@", "comment cells must have the text format"
        assert ws.cell(r, 5).protection.locked is False and ws.cell(r, 5).value is None
        assert ws.cell(r, 1).number_format == "General" and ws.cell(r, 2).number_format == "General"


# ---- rows that only look empty --------------------------------------------------------------------


@pytest.mark.parametrize("blank", [" ", "   ", " ", "\t\n"])
def test_white_space_below_the_last_row_is_not_a_row(tmp_path: Path, blank: str) -> None:
    p = write(tmp_path / "w.xlsx", comments=True)
    wb = openpyxl.load_workbook(p)
    wb["Ratings"]["C7"].value = blank  # an Accuracy cell one row below the four ratings
    wb["Ratings"]["E9"].value = blank
    wb.save(p)
    assert [r.row_number for r in read_workbook(p).rows] == [2, 3, 4, 5]


@pytest.mark.parametrize("content", [0, False, 4, "x", "=1+1"])
def test_real_content_below_the_last_row_is_still_reported_as_a_row(tmp_path: Path, content: object) -> None:
    p = write(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(p)
    wb["Ratings"]["C7"].value = content
    wb.save(p)
    rb = read_workbook(p)
    assert [r.row_number for r in rb.rows] == [2, 3, 4, 5, 7]
    assert rb.rows[-1].rating_id is None


# ---- _xHHHH_ sequences ----------------------------------------------------------------------------

# XLSX strings spell one character as _xHHHH_ and a literal underscore before such a sequence
# as _x005F_. These two functions are the format's rule, written here independently of the tool.


def excel_decodes(stored: str) -> str:
    import re

    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), stored)


def excel_stores(shown: str) -> str:
    import re

    return re.sub(r"_(?=x[0-9A-Fa-f]{4}_)", "_x005F_", shown)


XSTRING_TEXTS = [
    "The column is called Cost_x0020_Center and _x0041_ marks row A.",
    "adjacent _x0041_x0042_ sequences share an underscore",
    "_x0041_",
    "three in a row _x0041_x0042_x0043_ end",
    "already escaped _x005F_x0041_ stays as typed",
    "lower case hex _x00e9_ and upper _x00E9_",
    "not sequences: _x41_ _x00G1_ x0041_ _x0041 __ _x_",
    "an escaped control _x0001_ and half a pair _xD800_",
]


@pytest.mark.parametrize("text", XSTRING_TEXTS)
def test_text_with_escape_like_sequences_is_shown_literally_and_reads_back(tmp_path: Path, text: str) -> None:
    rws = [SheetRow(rating_id="AAAA-BBBB", text=text, context=text)]
    p = write(tmp_path / "w.xlsx", rws=rws, title=text, comments=True)
    ws = openpyxl.load_workbook(p)["Ratings"]
    stored = ws["C2"].value  # column order: rating_id, context, text
    assert stored == excel_stores(text), "the underscore of every sequence must be escaped"
    assert excel_decodes(stored) == text, "a spreadsheet program must show the text of the study"
    rb = read_workbook(p)
    assert rb.rows[0].text == text and rb.rows[0].context == text
    assert rb.meta["title"] == text, "the hidden description must survive too"
    assert text_sha256(rb.rows[0].text) == text_sha256(text)


def test_escape_like_sequence_is_escaped_in_the_file_itself(tmp_path: Path) -> None:
    import zipfile

    rws = [SheetRow(rating_id="AAAA-BBBB", text="total_x0020_cost", context=None)]
    p = write(tmp_path / "w.xlsx", rws=rws)
    with zipfile.ZipFile(p) as zf:
        xml = "".join(zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xml"))
    assert "total_x005F_x0020_cost" in xml and "total_x0020_cost" not in xml


def test_scale_label_and_nominal_value_with_a_sequence_read_back(tmp_path: Path) -> None:
    odd = scale_obj({"id": "odd", "label": "Field_x0041_name", "type": "nominal", "values": ["none", "mi_x0043_nor"]})
    p = write(tmp_path / "w.xlsx", scales=[odd])
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    assert ws["C1"].value == "Field_x005F_x0041_name"
    ws["C2"].value = "mi_x005F_x0043_nor"  # what a spreadsheet program stores for a picked value
    wb.save(p)
    rb = read_workbook(p)
    assert rb.meta["columns"][2] == {"header": "Field_x0041_name", "role": "scale", "scale_id": "odd"}
    assert rb.rows[0].values == {"odd": "mi_x0043_nor"}


def test_carriage_return_escape_in_a_returned_comment_is_decoded(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", comments=True)
    wb = openpyxl.load_workbook(p)
    ws = wb["Ratings"]
    ws["E2"].value = "first line_x000D_\nsecond line"  # how Excel stores a pasted CR LF
    ws["E3"].value = "old Mac_x000D_line ends"
    ws["E4"].value = "kept _x005F_x000D_ literal"
    wb.save(p)
    rows_ = read_workbook(p).rows
    assert rows_[0].comment == "first line\nsecond line"
    assert rows_[1].comment == "old Mac\nline ends"
    assert rows_[2].comment == "kept _x000D_ literal"


@pytest.mark.parametrize("hostile", ["_xD800_", "_xDFFF_", "_x0000_", "_x0001_", "_xFFFF_"])
def test_escapes_for_unstorable_characters_are_left_undecoded(tmp_path: Path, hostile: str) -> None:
    p = write(tmp_path / "w.xlsx", comments=True)
    wb = openpyxl.load_workbook(p)
    wb["Ratings"]["E2"].value = f"before {hostile} after"
    wb["Ratings"]["B2"].value = f"text {hostile}"
    wb.save(p)
    row = read_workbook(p).rows[0]
    assert row.comment == f"before {hostile} after"
    assert row.text == f"text {hostile}"
    text_sha256(row.text).encode("ascii")  # must be hashable: no lone surrogate reaches UTF-8
    row.comment.encode("utf-8")


def test_formula_text_is_not_decoded(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", comments=True)
    wb = openpyxl.load_workbook(p)
    wb["Ratings"]["E2"].value = '="_x0041_"'
    wb["Ratings"]["C2"].value = '="_x0041_"'
    wb.save(p)
    row = read_workbook(p).rows[0]
    assert row.comment == '="_x0041_"' and row.values["accuracy"] == '="_x0041_"'
    assert set(row.formula_headers) == {"Accuracy", "comment"}


# ---- the Instructions sheet describes this sheet, not every possible sheet ---------------------------

TONE = {"id": "tone", "label": "Tone", "type": "ordinal", "min": 1, "max": 3, "required": False}
REQUIRED_YES = "Required = yes: the study team needs a rating in that column for every row"
REQUIRED_NO = "Required = no: you may leave that cell empty."


def required_notes(path: Path) -> list[str]:
    return [line for line in instructions_blob(path).split("\n") if line.startswith("Required = ")]


def test_no_note_about_required_scales_when_no_scale_is_required(tmp_path: Path) -> None:
    optional = [scale_obj(dict(ACC, required=False)), scale_obj(TONE)]
    notes = required_notes(write(tmp_path / "w.xlsx", scales=optional))
    assert notes == [REQUIRED_NO], "the table says 'no' for every scale; the note must explain 'no' only"


def test_both_notes_when_some_scales_are_required_and_some_are_not(tmp_path: Path) -> None:
    notes = required_notes(write(tmp_path / "w.xlsx", scales=[scale_obj(ACC), scale_obj(TONE)]))
    assert len(notes) == 2 and notes[0].startswith(REQUIRED_YES) and notes[1] == REQUIRED_NO


def how_to_steps(path: Path) -> list[str]:
    lines = instructions_blob(path).split("\n")
    start, end = lines.index("How to fill in this sheet"), lines.index("Scales")
    return lines[start + 1 : end]


def test_instructions_explain_the_comment_column_when_there_is_one(tmp_path: Path) -> None:
    steps = how_to_steps(write(tmp_path / "w.xlsx", comments=True))
    assert [s.split(". ", 1)[0] for s in steps] == ["1", "2", "3", "4", "5"], steps
    assert steps[1].startswith("2. Fill in the highlighted columns only")
    assert steps[2] == (
        "3. The comment column is optional; use it for anything the study team should know about a row."
    )
    assert steps[4].startswith("5. Save this same file")


def test_instructions_do_not_mention_a_comment_column_that_does_not_exist(tmp_path: Path) -> None:
    p = write(tmp_path / "w.xlsx", comments=False)
    steps = how_to_steps(p)
    assert [s.split(". ", 1)[0] for s in steps] == ["1", "2", "3", "4"], steps
    assert "comment" not in instructions_blob(p).lower()


# ---- a text is never cut off to fit a cell ---------------------------------------------------------

ESCAPE_START = "See column total_x0041_ in the table. "


def test_text_that_fits_only_before_escaping_is_refused_not_cut_off(tmp_path: Path) -> None:
    t = ESCAPE_START + "y" * (32767 - len(ESCAPE_START) - len(" FINAL SENTENCE")) + " FINAL SENTENCE"
    assert len(t) == 32767
    p = tmp_path / "w.xlsx"
    with pytest.raises(RatersheetError, match="rating ABCD-2345.*_xHHHH_.*32767"):
        write(p, rws=[SheetRow(rating_id="ABCD-2345", text=t, context=None)])
    assert not p.exists()


def test_longest_text_with_an_escape_like_sequence_round_trips_whole(tmp_path: Path) -> None:
    t = ESCAPE_START + "y" * (32761 - len(ESCAPE_START) - len(" FINAL SENTENCE")) + " FINAL SENTENCE"
    p = write(tmp_path / "w.xlsx", rws=[SheetRow(rating_id="ABCD-2345", text=t, context=None)])
    stored = openpyxl.load_workbook(p)["Ratings"]["B2"].value
    assert len(stored) == 32767 and excel_decodes(stored) == t, "a rater must see the whole text"
    assert read_workbook(p).rows[0].text == t


def test_writer_itself_refuses_a_string_that_would_be_cut_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # openpyxl shortens a longer string to 32,767 characters without a word. The writer must not
    # depend on every caller having measured the text the way it is stored.
    import sys

    monkeypatch.setattr(sys.modules["ratersheet.workbook"], "cell_text_problem", lambda text: None)
    t = ESCAPE_START + "y" * (32767 - len(ESCAPE_START))
    with pytest.raises(RatersheetError, match="the text of rating ABCD-2345 takes 32773 characters to store"):
        write(tmp_path / "w.xlsx", rws=[SheetRow(rating_id="ABCD-2345", text=t, context=None)])
