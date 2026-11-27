"""Load and validate a study: the TOML study file and the CSV items file.

Everything is checked here, before any sheet exists, because a mistake that is
found after sheets have been sent to raters costs a whole round of rating.
Each failure is a ``ConfigError`` that names the file and the key or row.
"""

from __future__ import annotations

import csv
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

MAX_CELL_CHARS = 32_767  # Excel's limit for one cell
MAX_ORDINAL_POINTS = 11
MAX_DROPDOWN_CHARS = 255  # Excel's limit for a drop-down list typed into a validation rule
FIXED_HEADERS = ("rating_id", "context", "text", "comment")

_RATER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_SCALE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{n}" for n in range(1, 10)]
    + [f"LPT{n}" for n in range(1, 10)]
)
# Control characters other than tab, line feed and carriage return, plus the
# code points XML forbids. openpyxl refuses the first group when writing and
# cannot read back a workbook that contains the second.
_UNSTORABLE_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_LINE_BREAK_RE = re.compile("[\n\r\x85\u2028\u2029]")
# XLSX strings use ``_xHHHH_`` (four hex digits) as an escape for one character,
# and ``_x005F_`` for an underscore that would otherwise start such an escape.
# This matches each underscore the workbook writer has to spell that way. A
# lookahead, not a match of the whole sequence: in "_x0041_x0042_" the
# underscore that closes one sequence also opens the next.
XSTRING_ESCAPE_START_RE = re.compile(r"_(?=x[0-9A-Fa-f]{4}_)")
XSTRING_ESCAPED_UNDERSCORE = "_x005F_"
_UTF8_BOM = b"\xef\xbb\xbf"
# The csv module refuses a field longer than 131,072 characters before the
# tool can say which item is too long, so the limit is lifted while the items
# file is read. Not sys.maxsize: that overflows a C long on Windows.
_CSV_FIELD_LIMIT = 2**31 - 1

_TOP_LEVEL_KEYS = ("study", "raters", "design", "items", "scale", "comments")
_STUDY_KEYS = ("title", "seed", "instructions")
_RATERS_KEYS = ("ids",)
_DESIGN_KEYS = ("raters_per_item", "group_by_case")
_ITEMS_KEYS = ("file",)
_SCALE_KEYS = ("id", "label", "type", "min", "max", "values", "description", "required")
_COMMENTS_KEYS = ("enabled",)

_REQUIRED_COLUMNS = ("item_id", "source", "text")
_OPTIONAL_COLUMNS = ("case_id", "context")


@dataclass(frozen=True)
class Scale:
    """One thing a rater scores for every item."""

    id: str
    label: str
    type: str  # "ordinal" or "nominal"
    values: tuple[int | str, ...]  # ordinal: the integers min..max; nominal: the strings
    description: str
    required: bool


@dataclass(frozen=True)
class Item:
    """One text to be rated, with the source that raters must not see."""

    item_id: str
    source: str
    text: str
    case_id: str | None
    context: str | None


@dataclass(frozen=True)
class Study:
    """A validated study: settings, scales and items."""

    title: str
    seed: int
    instructions: str
    raters: tuple[str, ...]
    raters_per_item: int
    group_by_case: bool
    scales: tuple[Scale, ...]
    comments: bool
    items: tuple[Item, ...]
    ignored_columns: tuple[str, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        """The distinct source labels, sorted."""
        return tuple(sorted({item.source for item in self.items}))


def cell_text_problem(text: str) -> str | None:
    """Return why ``text`` cannot be stored in one XLSX cell, or ``None`` if it can.

    The result is phrased to follow the name of the thing, as in
    "text is 40000 characters long; ...".

    The length that counts is the length as stored: the workbook writer spells
    the underscore of every literal ``_xHHHH_`` sequence as ``_x005F_``, and a
    stored string beyond the limit would be cut off without a word.
    """
    n_sequences = len(XSTRING_ESCAPE_START_RE.findall(text))
    extra_per_sequence = len(XSTRING_ESCAPED_UNDERSCORE) - 1
    if len(text) + extra_per_sequence * n_sequences > MAX_CELL_CHARS:
        escapes = ""
        if n_sequences and len(text) <= MAX_CELL_CHARS:
            escapes = (
                f" and contains {n_sequences} literal _xHHHH_ "
                f"{'sequence' if n_sequences == 1 else 'sequences'} (an underscore, x, four "
                "hexadecimal digits, an underscore), and each such sequence takes "
                f"{extra_per_sequence} more characters to store"
            )
        return (
            f"is {len(text)} characters long{escapes}; a spreadsheet cell holds at most "
            f"{MAX_CELL_CHARS} characters"
        )
    found = _UNSTORABLE_RE.search(text)
    if found:
        return (
            f"contains the character U+{ord(found.group()):04X} at position "
            f"{found.start() + 1}, which cannot be stored in a spreadsheet cell"
        )
    return None


def load_study(path: str | os.PathLike[str]) -> Study:
    """Read a study file and its items file and return the validated study."""
    study_path = Path(path)
    data = _read_toml(study_path)
    try:
        _reject_unknown(data, _TOP_LEVEL_KEYS, "the top level of the file")
        title, seed, instructions = _parse_study_table(data)
        raters = _parse_raters(data)
        raters_per_item, group_by_case = _parse_design(data, len(raters))
        items_file = _parse_items_file(data)
        scales = _parse_scales(data)
        comments = _parse_comments(data)
    except ConfigError as exc:
        raise ConfigError(f"{study_path}: {exc}") from None

    items_path = study_path.parent / items_file
    try:
        items, ignored_columns = _read_items(items_path)
    except ConfigError as exc:
        raise ConfigError(f"{items_path}: {exc}") from None

    if group_by_case:
        without_case = [item.item_id for item in items if item.case_id is None]
        if without_case:
            raise ConfigError(
                f"{study_path}: [design] group_by_case = true needs a case_id for every item, "
                f"but item_id {without_case[0]!r} in {items_path} has none"
            )
    return Study(
        title=title,
        seed=seed,
        instructions=instructions,
        raters=raters,
        raters_per_item=raters_per_item,
        group_by_case=group_by_case,
        scales=scales,
        comments=comments,
        items=items,
        ignored_columns=ignored_columns,
    )


# --- the study file -------------------------------------------------------


def _read_toml(path: Path) -> dict:
    """Parse the study file, turning every way it can fail into a ``ConfigError``.

    A leading byte order mark is dropped: Windows editors write one when asked
    for "UTF-8", the TOML parser refuses it, and its complaint about line 1,
    column 1 points at a character nobody can see.
    """
    try:
        return tomllib.loads(path.read_bytes().removeprefix(_UTF8_BOM).decode("utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: study file not found") from None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read the study file ({exc})") from None
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: the study file is not valid UTF-8") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML ({exc})") from None
    except ValueError as exc:
        # Not a syntax error: Python refuses, for example, to convert an
        # integer of several thousand digits. Its own message is cut short
        # because it goes on to advise changing an interpreter setting.
        raise ConfigError(
            f"{path}: a value in the study file cannot be read ({_show(str(exc))}); "
            "a number with thousands of digits is the usual cause"
        ) from None
    except RecursionError:
        raise ConfigError(
            f"{path}: arrays or tables in the study file are nested too deeply to be read"
        ) from None


def _show(value: object) -> str:
    """Describe a wrong value briefly for an error message."""
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _reject_unknown(table: dict, allowed: tuple[str, ...], where: str) -> None:
    """Refuse keys the tool does not know, so that a typo is not silently ignored."""
    for key in table:
        if key not in allowed:
            raise ConfigError(
                f"unknown key {key!r} in {where}; allowed here: {', '.join(allowed)}"
            )


def _table(data: dict, name: str, allowed: tuple[str, ...], *, required: bool) -> dict:
    if name not in data:
        if required:
            raise ConfigError(f"the [{name}] table is missing")
        return {}
    table = data[name]
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table, got {_show(table)}")
    _reject_unknown(table, allowed, f"[{name}]")
    return table


def _is_integer(value: object) -> bool:
    """True for an int that is not a bool (TOML ``true`` is not a number)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _string(
    table: dict, key: str, where: str, *, default: str | None = None, single_line: bool = False
) -> str:
    """Return a string setting that ends up in a sheet, so it must fit in a cell.

    ``single_line`` is for the strings that are also written where a line
    break would split them: a heading, a column header, a list.
    """
    if key not in table:
        if default is None:
            raise ConfigError(f"{where} {key} is required")
        return default
    value = table[key]
    if not isinstance(value, str):
        raise ConfigError(f"{where} {key} must be a string, got {_show(value)}")
    problem = cell_text_problem(value)
    if problem is None and single_line:
        problem = _line_break_problem(value)
    if problem:
        raise ConfigError(f"{where} {key} {problem}")
    return value


def _line_break_problem(text: str) -> str | None:
    """Return why ``text`` cannot be used as a one-line label, or ``None`` if it can."""
    found = _LINE_BREAK_RE.search(text)
    if found:
        return (
            f"contains a line break at position {found.start() + 1}; it is used as a heading, "
            "a column header or a list entry, so it must be on one line"
        )
    return None


def _boolean(table: dict, key: str, where: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where} {key} must be true or false, got {_show(value)}")
    return value


def _integer(table: dict, key: str, where: str) -> int:
    if key not in table:
        raise ConfigError(f"{where} {key} is required")
    value = table[key]
    if not _is_integer(value):
        raise ConfigError(f"{where} {key} must be an integer, got {_show(value)}")
    return value


def _parse_study_table(data: dict) -> tuple[str, int, str]:
    table = _table(data, "study", _STUDY_KEYS, required=True)
    title = _string(table, "title", "[study]", single_line=True)
    if not title.strip():
        raise ConfigError("[study] title must not be empty")
    seed = _integer(table, "seed", "[study]")
    instructions = _string(table, "instructions", "[study]", default="")
    return title, seed, instructions


def _parse_raters(data: dict) -> tuple[str, ...]:
    table = _table(data, "raters", _RATERS_KEYS, required=True)
    ids = table.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ConfigError("[raters] ids must be a list of one or more rater ids")
    seen: set[str] = set()
    for rater_id in ids:
        if not isinstance(rater_id, str) or not _RATER_ID_RE.fullmatch(rater_id):
            raise ConfigError(
                f"[raters] ids: {_show(rater_id)} is not a valid rater id; use 1 to 32 "
                "letters, digits, underscores or hyphens (the id becomes a file name)"
            )
        if rater_id.upper() in _RESERVED_DEVICE_NAMES:
            raise ConfigError(
                f"[raters] ids: {rater_id!r} is a reserved file name on Windows; choose another id"
            )
        if rater_id.lower() in seen:
            raise ConfigError(
                f"[raters] ids: {rater_id!r} appears more than once (ids are compared "
                "ignoring case because they become file names)"
            )
        seen.add(rater_id.lower())
    return tuple(ids)


def _parse_design(data: dict, n_raters: int) -> tuple[int, bool]:
    table = _table(data, "design", _DESIGN_KEYS, required=False)
    raters_per_item = n_raters
    if "raters_per_item" in table:
        raters_per_item = _integer(table, "raters_per_item", "[design]")
        if not 1 <= raters_per_item <= n_raters:
            raise ConfigError(
                f"[design] raters_per_item must be between 1 and the number of raters "
                f"({n_raters}), got {raters_per_item}"
            )
    group_by_case = _boolean(table, "group_by_case", "[design]", default=False)
    return raters_per_item, group_by_case


def _parse_items_file(data: dict) -> str:
    table = _table(data, "items", _ITEMS_KEYS, required=True)
    file_name = table.get("file")
    if not isinstance(file_name, str) or not file_name.strip():
        raise ConfigError("[items] file is required and must be the path of the items CSV file")
    return file_name


def _parse_comments(data: dict) -> bool:
    table = _table(data, "comments", _COMMENTS_KEYS, required=False)
    return _boolean(table, "enabled", "[comments]", default=False)


def _parse_scales(data: dict) -> tuple[Scale, ...]:
    tables = data.get("scale")
    if not isinstance(tables, list) or not tables:
        raise ConfigError("at least one [[scale]] table is required")
    scales = tuple(_parse_scale(table, number) for number, table in enumerate(tables, start=1))
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for scale in scales:
        where = f"[[scale]] {scale.id!r}:"
        if scale.id in seen_ids:
            raise ConfigError(f"{where} id is used by more than one scale")
        if scale.label in FIXED_HEADERS:
            raise ConfigError(
                f"{where} label {scale.label!r} is the header of a fixed column; "
                f"labels may not be any of {', '.join(FIXED_HEADERS)}"
            )
        if scale.label in seen_labels:
            raise ConfigError(f"{where} label {scale.label!r} is used by more than one scale")
        seen_ids.add(scale.id)
        seen_labels.add(scale.label)
    return scales


def _parse_scale(table: object, number: int) -> Scale:
    where = f"[[scale]] number {number}:"
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table, got {_show(table)}")
    _reject_unknown(table, _SCALE_KEYS, f"[[scale]] number {number}")
    scale_id = table.get("id")
    if not isinstance(scale_id, str) or not _SCALE_ID_RE.fullmatch(scale_id):
        raise ConfigError(
            f"{where} id must start with a lowercase letter and contain only lowercase "
            f"letters, digits and underscores (at most 32 characters), got {_show(scale_id)}"
        )
    where = f"[[scale]] {scale_id!r}:"
    scale_type = table.get("type")
    values: tuple[int | str, ...]
    if scale_type == "ordinal":
        values = _ordinal_values(table, where)
    elif scale_type == "nominal":
        values = _nominal_values(table, where)
    else:
        raise ConfigError(f'{where} type must be "ordinal" or "nominal", got {_show(scale_type)}')
    label = _string(table, "label", where, default=scale_id, single_line=True)
    if not label.strip():
        raise ConfigError(f"{where} label must not be empty")
    return Scale(
        id=scale_id,
        label=label,
        type=scale_type,
        values=values,
        description=_string(table, "description", where, default=""),
        required=_boolean(table, "required", where, default=True),
    )


def _ordinal_values(table: dict, where: str) -> tuple[int, ...]:
    if "values" in table:
        raise ConfigError(f"{where} values is not allowed for an ordinal scale; use min and max")
    low = _integer(table, "min", where)
    high = _integer(table, "max", where)
    if low >= high:
        raise ConfigError(f"{where} min must be smaller than max, got min = {low}, max = {high}")
    if high - low + 1 > MAX_ORDINAL_POINTS:
        raise ConfigError(
            f"{where} min..max spans {high - low + 1} points; "
            f"an ordinal scale has at most {MAX_ORDINAL_POINTS}"
        )
    return tuple(range(low, high + 1))


def _nominal_values(table: dict, where: str) -> tuple[str, ...]:
    for key in ("min", "max"):
        if key in table:
            raise ConfigError(f"{where} {key} is not allowed for a nominal scale; use values")
    values = table.get("values")
    if not isinstance(values, list) or len(values) < 2:
        raise ConfigError(f"{where} values must be a list of two or more strings")
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where} values must be non-empty strings, got {_show(value)}")
        if value != value.strip():
            raise ConfigError(f"{where} values: {value!r} has leading or trailing spaces")
        if "," in value:
            raise ConfigError(
                f"{where} values: {value!r} contains a comma, which a spreadsheet "
                "drop-down list cannot hold"
            )
        problem = cell_text_problem(value) or _line_break_problem(value)
        if problem:
            raise ConfigError(f"{where} values: {_show(value)} {problem}")
        if value.casefold() in seen:
            raise ConfigError(
                f"{where} values: {value!r} appears more than once (values are compared "
                "ignoring case)"
            )
        seen.add(value.casefold())
    if len(",".join(values)) > MAX_DROPDOWN_CHARS:
        raise ConfigError(
            f"{where} values are {len(','.join(values))} characters when joined with commas; "
            f"a spreadsheet drop-down list holds at most {MAX_DROPDOWN_CHARS}"
        )
    return tuple(values)


# --- the items file -------------------------------------------------------


def _read_csv_records(path: Path) -> list[list[str]]:
    """Read every record of the items file (``utf-8-sig`` drops a leading BOM).

    The file is read strictly. The csv module's lenient default accepts a
    quote mark in the wrong place by dropping it, and an opening quote that is
    never closed by taking the rows that follow into one text. The texts
    raters see would then differ from the file without any sign of it.
    """
    previous_limit = csv.field_size_limit(_CSV_FIELD_LIMIT)
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                return list(reader)
            except csv.Error as exc:
                raise ConfigError(
                    f"the items file is not valid CSV at line {reader.line_num} ({exc}); "
                    "a field that starts with a double quote must end with one, and a double "
                    'quote inside a quoted text must be written twice (""). A quote that is '
                    "never closed is reported at the last line of the file"
                ) from None
    except FileNotFoundError:
        raise ConfigError("items file not found (set in [items] file)") from None
    except OSError as exc:
        raise ConfigError(f"cannot read the items file ({exc})") from None
    except UnicodeDecodeError:
        raise ConfigError("the items file is not valid UTF-8; save it as 'CSV UTF-8'") from None
    except ValueError as exc:
        # The csv module refuses a NUL character with a bare ValueError.
        raise ConfigError(f"the items file cannot be read as CSV ({exc})") from None
    finally:
        csv.field_size_limit(previous_limit)


def _header_hint(record: list[str]) -> str:
    """Return a hint at why the header row holds no required column, or ``""``.

    The usual causes leave the header looking right to the person who wrote
    it: a spreadsheet program that separates with semicolons or tabs, or a
    file saved as UTF-16. A hint is given only when splitting the header on
    that separator yields every required column, so a comma-separated file
    with a column really missing gets none.
    """
    line = ",".join(record)
    if "\x00" in line:
        return "; the file looks like UTF-16, save it as 'CSV UTF-8'"
    for separator, name in ((";", "semicolons"), ("\t", "tabs")):
        names = {part.strip().strip('"') for part in line.split(separator)}
        if names.issuperset(_REQUIRED_COLUMNS):
            return f"; the file is separated by {name}, save it with commas as the separator"
    return ""


def _parse_header(record: list[str]) -> list[str]:
    header = [name.strip() for name in record]
    for name in _REQUIRED_COLUMNS:
        if name not in header:
            raise ConfigError(
                f"the required column {name!r} is missing from the header row, which was "
                f"read as {_show(header)}{_header_hint(record)}"
            )
    for name in header:
        if name and header.count(name) > 1:
            raise ConfigError(f"the column {name!r} appears more than once in the header row")
    return header


def _read_items(path: Path) -> tuple[tuple[Item, ...], tuple[str, ...]]:
    """Return the items and the names of the columns that were ignored."""
    records = _read_csv_records(path)
    if not records:
        raise ConfigError("the items file is empty; it needs a header row and at least 2 items")
    header = _parse_header(records[0])
    items: list[Item] = []
    first_row_of: dict[str, int] = {}
    first_spelling_of: dict[str, tuple[str, int]] = {}  # casefolded source -> (source, row)
    # Row numbers count the header as row 1, as a spreadsheet shows them.
    for row_number, record in enumerate(records[1:], start=2):
        if not any(field.strip() for field in record):
            continue
        if len(record) != len(header):
            raise ConfigError(
                f"row {row_number} has {len(record)} fields but the header has {len(header)}; "
                "a text that contains a comma or a line break must be enclosed in double quotes"
            )
        item = _parse_item(dict(zip(header, record)), row_number)
        if item.item_id in first_row_of:
            raise ConfigError(
                f"row {row_number}: item_id {item.item_id!r} is already used in "
                f"row {first_row_of[item.item_id]}; item ids must be unique"
            )
        first_row_of[item.item_id] = row_number
        # "GPT-4" and "gpt-4" would silently become two sources, each balanced
        # and leak-checked on its own; rater ids and nominal values are held
        # to the same rule.
        spelling, spelling_row = first_spelling_of.setdefault(
            item.source.casefold(), (item.source, row_number)
        )
        if spelling != item.source:
            raise ConfigError(
                f"row {row_number}: source {item.source!r} differs only in upper and lower "
                f"case from {spelling!r} in row {spelling_row}; write one source the same way "
                "in every row"
            )
        items.append(item)
    if len(items) < 2:
        raise ConfigError(f"the items file has {len(items)} item(s); at least 2 are needed")
    known = _REQUIRED_COLUMNS + _OPTIONAL_COLUMNS
    ignored = tuple(name for name in header if name and name not in known)
    return tuple(items), ignored


def _group_label(raw: str) -> str:
    """Return a source or case id in the form it is compared and sorted in.

    Items are grouped by these labels, and the order of the sources feeds the
    assignment. The same label typed with composed accents in one row and
    decomposed ones in another (file names on macOS are decomposed) must be
    one group, so labels are kept in NFC, as texts are before hashing.
    """
    return unicodedata.normalize("NFC", raw.strip())


def _parse_item(fields: dict[str, str], row_number: int) -> Item:
    item_id = fields["item_id"].strip()
    if not item_id:
        raise ConfigError(f"row {row_number}: item_id is empty")
    where = f"row {row_number} (item_id {item_id!r}):"
    source = _group_label(fields["source"])
    if not source:
        raise ConfigError(f"{where} source is empty")
    text = fields["text"]
    if not text.strip():
        raise ConfigError(f"{where} text is empty")
    context = fields.get("context") or None
    for name, value in (("text", text), ("context", context)):
        problem = cell_text_problem(value) if value is not None else None
        if problem:
            raise ConfigError(f"{where} {name} {problem}")
    return Item(
        item_id=item_id,
        source=source,
        text=text,
        case_id=_group_label(fields.get("case_id", "")) or None,
        context=context,
    )
