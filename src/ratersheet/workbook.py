"""Write one rater's workbook, and read back a workbook a rater has returned.

A returned workbook comes from someone else's computer and is untrusted.
Nothing in it is evaluated: it is opened with formulas shown as text, every
cell is treated as data, and anything unexpected is reported, not repaired.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from . import __version__
from ._store import FORMAT_VERSION
from .config import (
    MAX_CELL_CHARS,
    XSTRING_ESCAPE_START_RE,
    XSTRING_ESCAPED_UNDERSCORE,
    Scale,
    cell_text_problem,
)
from .errors import RatersheetError

INSTRUCTIONS_SHEET = "Instructions"
RATINGS_SHEET = "Ratings"
META_SHEET = "_meta"
MAX_DATA_ROWS = 100_000  # a returned sheet that claims more rows than this is refused

_ROLES = ("rating_id", "context", "text", "scale", "comment")
_COLUMN_WIDTHS = {"rating_id": 14, "context": 45, "text": 80, "comment": 40}
_INSTRUCTION_WIDTHS = (26, 30, 60, 10)
_CHARS_PER_INSTRUCTION_LINE = 110  # a cautious estimate for the merged width above
_LINE_HEIGHT_POINTS = 16
_ROW_PADDING_POINTS = 4
_MAX_ROW_HEIGHT_POINTS = 409  # Excel's limit
_MAX_VALIDATION_MESSAGE_CHARS = 255  # Excel's limit
_TEXT_NUMBER_FORMAT = "@"

# An XLSX string escape for one character, ``_xHHHH_`` (four hex digits). How
# a literal one is written is defined beside the length check in ``config``.
_XSTRING_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")

_BOLD = Font(bold=True)
_TITLE_FONT = Font(bold=True, size=14)
_WRAP_TOP = Alignment(wrap_text=True, vertical="top")
_INPUT_FILL = PatternFill(fill_type="solid", start_color="FFF2CC", end_color="FFF2CC")
_INPUT_EDGE = Side(style="thin", color="BFBFBF")
_INPUT_BORDER = Border(left=_INPUT_EDGE, right=_INPUT_EDGE, top=_INPUT_EDGE, bottom=_INPUT_EDGE)
_UNLOCKED = Protection(locked=False)

_HOW_TO_FILL_IN = (
    'Open the sheet named "Ratings". Each row holds one text to rate.',
    "Fill in the highlighted columns only. Each rating cell offers a drop-down list of "
    "the allowed values.",
)
# The comment column is highlighted like the rating columns, so a sheet that
# has one says what it is for and that it may stay empty.
_COMMENT_STEP = (
    "The comment column is optional; use it for anything the study team should know about a row."
)
_HOW_TO_RETURN = (
    "Do not change any other cell, and do not add, delete or rename rows, columns or sheets. "
    "You may widen columns and rows to read comfortably.",
    "Save this same file, in the same .xlsx format, and return it.",
)
# The drop-down lists accept an empty cell for every scale, so that a rater can
# clear a cell. These lines say what the "Required" column of the scale table
# asks for without denying that; each is written only if a scale has that answer.
_REQUIRED_NOTE = (
    "Required = yes: the study team needs a rating in that column for every row; "
    "an empty cell counts as missing."
)
_OPTIONAL_NOTE = "Required = no: you may leave that cell empty."


@dataclass(frozen=True)
class SheetRow:
    """What one row of a rater's sheet shows."""

    rating_id: str
    text: str
    context: str | None


@dataclass(frozen=True)
class ReturnedRow:
    """One non-empty data row of a returned sheet, exactly as found."""

    rating_id: str | None
    text: str | None
    context: str | None
    values: dict[str, object]  # scale_id -> raw cell value (None if empty)
    comment: str | None
    row_number: int  # spreadsheet row, for messages
    # Headers of the columns whose cell in this row held a formula. A formula
    # is shown as its text and never evaluated; this records that it was one,
    # so that "=1+1" typed as a formula is not mistaken for the text "=1+1".
    formula_headers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReturnedWorkbook:
    """A returned workbook: where it is, what it says about itself, and its rows."""

    path: Path
    meta: dict  # parsed _meta JSON
    rows: tuple[ReturnedRow, ...]


# --- writing --------------------------------------------------------------


def write_workbook(
    path: str | os.PathLike[str],
    *,
    title: str,
    instructions: str,
    rater_id: str,
    rows: Sequence[SheetRow],
    scales: Sequence[Scale],
    comments: bool,
    key_sha256: str,
    has_context: bool | None = None,
) -> None:
    """Write the workbook for one rater.

    ``has_context`` says whether the sheet gets a context column. All sheets
    of a study should agree on this, so ``build`` passes the study-wide answer;
    when it is left out the column is added if any of ``rows`` has a context.
    """
    if has_context is None:
        has_context = any(row.context is not None for row in rows)
    columns = sheet_columns(scales, has_context=has_context, comments=comments)
    meta = {
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "title": title,
        "rater_id": rater_id,
        "key_sha256": key_sha256,
        "columns": columns,
    }
    workbook = openpyxl.Workbook()
    instructions_sheet = workbook.active
    instructions_sheet.title = INSTRUCTIONS_SHEET
    _fill_instructions(
        instructions_sheet, title, instructions, rater_id, scales, comments, key_sha256
    )
    _fill_ratings(workbook.create_sheet(RATINGS_SHEET), columns, rows, scales)
    meta_sheet = workbook.create_sheet(META_SHEET)
    meta_json = json.dumps(meta, sort_keys=True, ensure_ascii=False)
    _set_text(meta_sheet["A1"], meta_json, "the sheet description (title and scale labels)")
    meta_sheet.sheet_state = "veryHidden"
    workbook.save(path)


def sheet_columns(scales: Sequence[Scale], *, has_context: bool, comments: bool) -> list[dict]:
    """Describe the columns of the Ratings sheet, in order, as stored in ``_meta``.

    ``collect`` calls this too: a returned sheet is only used if the columns
    it records are exactly the ones the study's sheets were written with.
    """
    columns: list[dict] = [{"header": "rating_id", "role": "rating_id"}]
    if has_context:
        columns.append({"header": "context", "role": "context"})
    columns.append({"header": "text", "role": "text"})
    for scale in scales:
        columns.append({"header": scale.label, "role": "scale", "scale_id": scale.id})
    if comments:
        columns.append({"header": "comment", "role": "comment"})
    return columns


def _set_text(cell: Cell, text: str, what: str) -> None:
    """Store ``text`` in ``cell`` as an explicit string, never as a formula.

    openpyxl turns a value that starts with "=" into a formula unless the cell
    type is set back to string afterwards. Every text the tool writes goes
    through here so that no study content can become a formula in a rater's
    spreadsheet program. Line endings are written as ``\\n`` because XLSX
    cannot carry a bare carriage return; the text hashes ignore that
    difference. A text that would not survive is refused, not truncated.

    A text that happens to contain ``_xHHHH_`` would be read by a spreadsheet
    program as an escaped character, so the rater would see another text than
    the one in the study. The underscore that starts such a sequence is
    therefore written as ``_x005F_``, which is how the format spells a literal
    one; ``_decode_xstring`` undoes this when a sheet is read back.
    """
    problem = cell_text_problem(text)
    if problem:
        raise RatersheetError(f"cannot write the workbook: {what} {problem}")
    unix_text = text.replace("\r\n", "\n").replace("\r", "\n")
    stored = XSTRING_ESCAPE_START_RE.sub(XSTRING_ESCAPED_UNDERSCORE, unix_text)
    # ``cell_text_problem`` already counts the escapes. This second check is
    # here because openpyxl cuts a longer string off without saying so, and a
    # rater must never be handed a shortened text.
    if len(stored) > MAX_CELL_CHARS:
        raise RatersheetError(
            f"cannot write the workbook: {what} takes {len(stored)} characters to store; "
            f"a spreadsheet cell holds at most {MAX_CELL_CHARS} characters"
        )
    cell.value = stored
    cell.data_type = "s"


def _add_prose(sheet: Worksheet, row: int, text: str, what: str, font: Font | None = None) -> int:
    """Write one block of prose across the instruction columns; return the next row.

    Merged cells do not grow with their content, so the row height is
    estimated from the length of the text.
    """
    sheet.merge_cells(
        start_row=row, start_column=1, end_row=row, end_column=len(_INSTRUCTION_WIDTHS)
    )
    cell = sheet.cell(row=row, column=1)
    _set_text(cell, text, what)
    cell.alignment = _WRAP_TOP
    if font is not None:
        cell.font = font
    lines = sum(
        max(1, math.ceil(len(line) / _CHARS_PER_INSTRUCTION_LINE)) for line in text.split("\n")
    )
    height = lines * _LINE_HEIGHT_POINTS + _ROW_PADDING_POINTS
    sheet.row_dimensions[row].height = min(_MAX_ROW_HEIGHT_POINTS, height)
    return row + 1


def _fill_instructions(
    sheet: Worksheet,
    title: str,
    instructions: str,
    rater_id: str,
    scales: Sequence[Scale],
    comments: bool,
    key_sha256: str,
) -> None:
    """Write what the rater reads first.

    The steps and notes are chosen from the sheet's own columns and scales, so
    that a rater is never told about something this sheet does not have.
    """
    for index, width in enumerate(_INSTRUCTION_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    row = _add_prose(sheet, 1, title, "the title", _TITLE_FONT)
    row = _add_prose(sheet, row, f"Rater: {rater_id}", "the rater id", _BOLD)
    if instructions.strip():
        row = _add_prose(sheet, row + 1, instructions, "the instructions")
    row = _add_prose(sheet, row + 1, "How to fill in this sheet", "a heading", _BOLD)
    steps = _HOW_TO_FILL_IN + ((_COMMENT_STEP,) if comments else ()) + _HOW_TO_RETURN
    for number, step in enumerate(steps, start=1):
        row = _add_prose(sheet, row, f"{number}. {step}", "a step")
    row = _add_prose(sheet, row + 1, "Scales", "a heading", _BOLD)
    row = _add_scale_table(sheet, row, scales)
    if any(scale.required for scale in scales):
        row = _add_prose(sheet, row, _REQUIRED_NOTE, "a note")
    if not all(scale.required for scale in scales):
        row = _add_prose(sheet, row, _OPTIONAL_NOTE, "a note")
    row = _add_prose(sheet, row + 1, f"Key commitment (SHA-256): {key_sha256}", "the commitment")
    row = _add_prose(
        sheet,
        row,
        "The key commitment is a fingerprint of the file that links each text to its source. "
        "It lets the study team show later that the file was not changed after this sheet "
        "was made.",
        "a note",
    )
    _add_prose(sheet, row + 1, f"Generated by ratersheet {__version__}", "the version line")


def _add_scale_table(sheet: Worksheet, first_row: int, scales: Sequence[Scale]) -> int:
    """Write the table of scales; return the row after it."""
    table = [("Scale", "Allowed values", "Description", "Required")]
    for scale in scales:
        allowed = ", ".join(str(value) for value in scale.values)
        table.append((scale.label, allowed, scale.description, "yes" if scale.required else "no"))
    for offset, line in enumerate(table):
        for column, text in enumerate(line, start=1):
            cell = sheet.cell(row=first_row + offset, column=column)
            _set_text(cell, text, f"the description of scale {line[0]!r}")
            cell.alignment = _WRAP_TOP
            if offset == 0:
                cell.font = _BOLD
    return first_row + len(table)


def _fill_ratings(
    sheet: Worksheet, columns: list[dict], rows: Sequence[SheetRow], scales: Sequence[Scale]
) -> None:
    scale_by_id = {scale.id: scale for scale in scales}
    last_row = len(rows) + 1
    for index, column in enumerate(columns, start=1):
        letter = get_column_letter(index)
        header = sheet.cell(row=1, column=index)
        _set_text(header, column["header"], "a column header")
        header.font = _BOLD
        header.alignment = _WRAP_TOP
        sheet.column_dimensions[letter].width = _COLUMN_WIDTHS.get(
            column["role"], min(30, max(12, len(column["header"]) + 4))
        )
        if column["role"] == "scale" and rows:
            dropdown = _dropdown(scale_by_id[column["scale_id"]])
            sheet.add_data_validation(dropdown)
            dropdown.add(f"{letter}2:{letter}{last_row}")
    for row_number, row in enumerate(rows, start=2):
        for index, column in enumerate(columns, start=1):
            cell = sheet.cell(row=row_number, column=index)
            _fill_ratings_cell(cell, column["role"], row)
            if column["role"] == "comment" or (
                column["role"] == "scale" and scale_by_id[column["scale_id"]].type == "nominal"
            ):
                # In a General cell a spreadsheet program re-types a picked or
                # typed TRUE, 0.5, 1-2 or 50% as a boolean, number or date, and
                # the scale's own value, or the rater's own words in a comment,
                # are lost. Ordinal cells stay General so that a typed 4
                # arrives as the number 4.
                cell.number_format = _TEXT_NUMBER_FORMAT
    sheet.freeze_panes = "A2"
    # Protection without a password: it prevents accidental edits of the texts
    # and codes; it is not security. Resizing stays allowed so that long texts
    # can be read comfortably.
    sheet.protection.sheet = True
    sheet.protection.formatColumns = False
    sheet.protection.formatRows = False


def _fill_ratings_cell(cell: Cell, role: str, row: SheetRow) -> None:
    """Fill one data cell: study content is locked text, the rest is for the rater."""
    cell.alignment = _WRAP_TOP
    where = f"of rating {row.rating_id}"
    if role == "rating_id":
        _set_text(cell, row.rating_id, f"the rating id {where}")
    elif role == "text":
        _set_text(cell, row.text, f"the text {where}")
    elif role == "context":
        if row.context is not None:
            _set_text(cell, row.context, f"the context {where}")
    else:
        cell.fill = _INPUT_FILL
        cell.border = _INPUT_BORDER
        cell.protection = _UNLOCKED


def _dropdown(scale: Scale) -> DataValidation:
    """Build the drop-down list of a scale's allowed values (blank stays allowed)."""
    allowed = [str(value) for value in scale.values]
    # The list is one quoted string inside the validation formula, so a quote
    # mark in a value is doubled.
    formula = '"' + ",".join(allowed).replace('"', '""') + '"'
    message = f"{scale.label}: choose one of {', '.join(allowed)}, or leave the cell empty."
    if len(message) > _MAX_VALIDATION_MESSAGE_CHARS:
        message = "Choose a value from the drop-down list, or leave the cell empty."
    return DataValidation(
        type="list",
        formula1=formula,
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Value not allowed",
        error=message,
    )


# --- reading --------------------------------------------------------------


def read_workbook(path: str | os.PathLike[str]) -> ReturnedWorkbook:
    """Read a returned workbook without evaluating anything in it."""
    file_path = Path(path)
    name = file_path.name
    workbook = _open(file_path)
    meta = _read_meta(_worksheet(workbook, META_SHEET, name), name)
    sheet = _worksheet(workbook, RATINGS_SHEET, name)
    positions = _locate_columns(sheet, meta["columns"], name)
    if sheet.max_row - 1 > MAX_DATA_ROWS:
        raise RatersheetError(
            f"{name}: the {RATINGS_SHEET} sheet extends to row {sheet.max_row}, far beyond any "
            "rating sheet; delete the stray cells below the last rating row and save again"
        )
    rows = []
    for row_number in range(2, sheet.max_row + 1):
        cells = [sheet.cell(row=row_number, column=position) for position in positions]
        if not all(_is_blank(cell.value) for cell in cells):
            rows.append(_returned_row(meta["columns"], cells, row_number))
    return ReturnedWorkbook(path=file_path, meta=meta, rows=tuple(rows))


def _is_blank(value: object) -> bool:
    """An empty cell or a cell with only white space is not content.

    Without this, a space typed below the last rating row would count as a row
    without a rating id and fail the whole collection. Truthiness is not the
    test: the number 0 and FALSE are content.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _decode_xstring(text: str) -> str:
    """Return a string cell's text with its ``_xHHHH_`` escapes decoded.

    Spreadsheet programs write a character that XML cannot carry (a carriage
    return, say) as ``_xHHHH_`` and a literal underscore before such a
    sequence as ``_x005F_``; openpyxl hands both over undecoded. One pass from
    the left decodes them the way the programs do. An escape for a character
    the tool itself would refuse to store (a control character, half of a
    surrogate pair) is left as it is, so that nothing a hostile sheet contains
    can reach the hashes or the CSV files as an unencodable character.
    """

    def decoded(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if cell_text_problem(character) is None else match.group()

    return _XSTRING_ESCAPE_RE.sub(decoded, text)


def _open(path: Path) -> openpyxl.Workbook:
    """Open an untrusted workbook with formulas visible as text.

    Any failure to parse means "this is not a workbook we can use", whatever
    exception the parser happens to raise for a damaged or hostile file, so
    every exception is turned into one clear error here.
    """
    if not openpyxl.DEFUSEDXML:
        raise RatersheetError(
            "refusing to open returned workbooks: the defusedxml package is not installed "
            "or has been disabled, and it is what makes parsing files from others safe"
        )
    if not path.is_file():
        raise RatersheetError(f"{path} does not exist or is not a file")
    try:
        return openpyxl.load_workbook(path, data_only=False, keep_links=False)
    except Exception as exc:  # see the docstring
        reason = f"{type(exc).__name__}: {exc}"[:200]
        raise RatersheetError(
            f"{path.name} cannot be read as an Excel workbook ({reason})"
        ) from None


def _worksheet(workbook: openpyxl.Workbook, sheet_name: str, name: str) -> Worksheet:
    if sheet_name not in workbook.sheetnames:
        raise RatersheetError(
            f"{name} has no sheet named {sheet_name!r}; it is not a workbook written by "
            "ratersheet, or the sheet was deleted or renamed"
        )
    sheet = workbook[sheet_name]
    if not isinstance(sheet, Worksheet):
        raise RatersheetError(f"{name}: the sheet {sheet_name!r} is not a worksheet")
    return sheet


def _read_meta(sheet: Worksheet, name: str) -> dict:
    raw = sheet["A1"].value
    if not isinstance(raw, str):
        raise RatersheetError(f"{name}: the hidden {META_SHEET} sheet is empty")
    try:
        meta = json.loads(_decode_xstring(raw))
    except (ValueError, RecursionError):
        raise RatersheetError(
            f"{name}: the hidden {META_SHEET} sheet does not hold valid JSON"
        ) from None
    problem = _meta_problem(meta)
    if problem:
        raise RatersheetError(f"{name}: the hidden {META_SHEET} sheet {problem}")
    return meta


def _meta_problem(meta: object) -> str | None:
    """Return what is wrong with the parsed ``_meta`` content, or ``None``.

    The rest of the tool relies on this shape, so it is checked once, here.
    """
    if not isinstance(meta, dict):
        return "does not hold a JSON object"
    if meta.get("format_version") != FORMAT_VERSION:
        return (
            f"has format_version {meta.get('format_version')!r}; this version of ratersheet "
            f"reads format_version {FORMAT_VERSION}"
        )
    for key in ("tool_version", "title", "rater_id", "key_sha256"):
        if not isinstance(meta.get(key), str):
            return f"has no text entry {key!r}"
    columns = meta.get("columns")
    if not isinstance(columns, list):
        return "has no list of columns"
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("header"), str):
            return "describes a column without a header"
        if column.get("role") not in _ROLES:
            return f"describes a column with the unknown role {column.get('role')!r}"
        if column["role"] == "scale" and not isinstance(column.get("scale_id"), str):
            return f"describes the scale column {column['header']!r} without a scale_id"
    roles = [column["role"] for column in columns]
    if roles.count("rating_id") != 1 or roles.count("text") != 1:
        return "must describe exactly one rating_id column and one text column"
    if roles.count("context") > 1 or roles.count("comment") > 1:
        return "describes more than one context or comment column"
    headers = [column["header"] for column in columns]
    scale_ids = [column["scale_id"] for column in columns if column["role"] == "scale"]
    if len(set(headers)) != len(headers) or len(set(scale_ids)) != len(scale_ids):
        return "describes the same column or scale twice"
    return None


def _locate_columns(sheet: Worksheet, columns: list[dict], name: str) -> list[int]:
    """Return the column number of each recorded column, found by its header."""
    found: dict[str, list[int]] = {}
    for cell in sheet[1]:
        if cell.data_type == "s" and isinstance(cell.value, str):
            found.setdefault(_decode_xstring(cell.value), []).append(cell.column)
    positions = []
    for column in columns:
        matches = found.get(column["header"], [])
        if len(matches) != 1:
            state = "is missing from" if not matches else "appears more than once in"
            raise RatersheetError(
                f"{name}: the column {column['header']!r} {state} row 1 of the "
                f"{RATINGS_SHEET} sheet"
            )
        positions.append(matches[0])
    return positions


def _cell_text(cell: Cell) -> str | None:
    """Return a cell's content as text (a formula as its formula text), or ``None``."""
    value = cell.value
    if value is None or value == "":
        return None
    if isinstance(value, str):
        # Formula text is shown as written; only stored strings carry escapes.
        return value if cell.data_type == "f" else _decode_xstring(value)
    # An array formula is an object that carries its text.
    formula_text = getattr(value, "text", None)
    return formula_text if isinstance(formula_text, str) else str(value)


def _returned_row(columns: list[dict], cells: list[Cell], row_number: int) -> ReturnedRow:
    fixed: dict[str, str | None] = dict.fromkeys(("rating_id", "text", "context", "comment"))
    values: dict[str, object] = {}
    formula_headers = []
    for column, cell in zip(columns, cells):
        is_formula = cell.data_type == "f"
        if is_formula:
            formula_headers.append(column["header"])
        if column["role"] == "scale":
            is_text = is_formula or isinstance(cell.value, str)
            values[column["scale_id"]] = _cell_text(cell) if is_text else cell.value
        else:
            fixed[column["role"]] = _cell_text(cell)
    # A decoded escape can put a carriage return into a comment. The comment
    # goes into a CSV file with "\n" line endings, so it gets the same ones.
    comment = (fixed["comment"] or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return ReturnedRow(
        rating_id=(fixed["rating_id"] or "").strip() or None,
        text=fixed["text"],
        context=fixed["context"],
        values=values,
        comment=comment or None,
        row_number=row_number,
        formula_headers=tuple(formula_headers),
    )
