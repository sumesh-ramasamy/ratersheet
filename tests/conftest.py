"""Shared helpers for the ratersheet tests.

Everything here is written from the written specification the library was built against (its behaviour is described in README.md and DESIGN.md), using only the
public API plus openpyxl for acting as a rater.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import openpyxl
import pytest

from ratersheet import Item, Scale, Study, build, load_study

SEED = 7391846
NONCE = "0123456789abcdef" * 4
CREATED = "2026-11-26T22:15:00Z"
REPO_URL = "https://github.com/sumesh-ramasamy/ratersheet"

# Source labels are deliberately odd words so that they cannot occur in any synthetic text
# by accident (and cannot be hex digits inside a hash).
SRC_A = "srcalpha"
SRC_B = "srcbeta"
SRC_C = "srcgamma"

DEFAULT_SCALES: list[dict[str, Any]] = [
    {
        "id": "accuracy",
        "label": "Accuracy",
        "type": "ordinal",
        "min": 1,
        "max": 5,
        "description": "1 = wrong, 5 = fully correct",
    },
    {"id": "harm", "type": "nominal", "values": ["none", "minor", "serious"]},
]

OPTIONAL_SCALES: list[dict[str, Any]] = DEFAULT_SCALES + [
    {"id": "tone", "label": "Tone", "type": "ordinal", "min": 1, "max": 3, "required": False},
]


# ---------------------------------------------------------------------------
# Items and study files
# ---------------------------------------------------------------------------


def make_item_rows(
    counts: dict[str, int] | None = None,
    *,
    with_case: bool = False,
    with_context: bool = False,
    cases_of: int = 2,
) -> list[dict[str, str]]:
    """Synthetic item rows, sources interleaved round-robin, lengths scattered."""
    counts = dict(counts or {SRC_A: 7, SRC_B: 7})
    remaining = dict(counts)
    order: list[str] = []
    while any(remaining.values()):
        for src in counts:
            if remaining[src] > 0:
                order.append(src)
                remaining[src] -= 1
    rows: list[dict[str, str]] = []
    for n, src in enumerate(order, start=1):
        pad = "detail " * ((n * 7919) % 11 + 1)
        row = {
            "item_id": f"it{n:04d}",
            "source": src,
            "text": f"Synthetic answer {n} about topic {n % 5}. {pad}".strip(),
        }
        if with_case:
            row["case_id"] = f"cs{(n - 1) // cases_of + 1:03d}"
        if with_context:
            row["context"] = f"Synthetic case note {n}."
        rows.append(row)
    return rows


def toml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    raise TypeError(f"cannot write {v!r} as TOML")


def _table(name: str, mapping: dict[str, Any]) -> str:
    out = [f"[{name}]"]
    for k, v in mapping.items():
        out.append(f"{k} = {toml_val(v)}")
    return "\n".join(out) + "\n\n"


def write_items_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: list[str] | None = None,
    *,
    bom: bool = False,
) -> None:
    columns = columns or list(rows[0].keys())
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_study(
    folder: Path,
    *,
    rows: list[dict[str, str]] | None = None,
    columns: list[str] | None = None,
    raters: list[str] | None = ("R1", "R2", "R3"),  # type: ignore[assignment]
    raters_table: dict[str, Any] | None = None,
    rpi: int | None = None,
    group_by_case: bool | None = None,
    design: dict[str, Any] | None = None,
    scales: list[dict[str, Any]] | None = None,
    comments: Any = None,
    seed: Any = SEED,
    title: Any = "Synthetic rating study",
    instructions: str | None = "Rate each text on its own merits.",
    study_extra: dict[str, Any] | None = None,
    items_table: dict[str, Any] | None = None,
    extra_toml: str = "",
    items_name: str = "items.csv",
    bom: bool = False,
    items_text: str | None = None,
    study_name: str = "study.toml",
) -> Path:
    """Write study.toml and items.csv into `folder`; return the study.toml path.

    `comments`: None omits the table; True/False writes `enabled`; a dict is written as is.
    A parameter set to None (title, seed, instructions, raters) is omitted from the file.
    """
    folder.mkdir(parents=True, exist_ok=True)
    rows = make_item_rows() if rows is None else rows
    scales = DEFAULT_SCALES if scales is None else scales

    study_tbl: dict[str, Any] = {}
    if title is not None:
        study_tbl["title"] = title
    if seed is not None:
        study_tbl["seed"] = seed
    if instructions is not None:
        study_tbl["instructions"] = instructions
    study_tbl.update(study_extra or {})
    text = _table("study", study_tbl)

    if raters_table is not None:
        text += _table("raters", raters_table)
    elif raters is not None:
        text += _table("raters", {"ids": list(raters)})

    dsg: dict[str, Any] = dict(design or {})
    if rpi is not None:
        dsg["raters_per_item"] = rpi
    if group_by_case is not None:
        dsg["group_by_case"] = group_by_case
    if dsg:
        text += _table("design", dsg)

    if items_table is None:
        items_table = {"file": items_name}
    if items_table:
        text += _table("items", items_table)

    for sc in scales:
        text += "[[scale]]\n"
        for k, v in sc.items():
            text += f"{k} = {toml_val(v)}\n"
        text += "\n"

    if comments is not None:
        if isinstance(comments, dict):
            text += _table("comments", comments)
        else:
            text += _table("comments", {"enabled": bool(comments)})
    text += extra_toml

    path = folder / study_name
    path.write_text(text, encoding="utf-8", newline="\n")
    if items_text is not None:
        (folder / items_name).write_text(items_text, encoding="utf-8", newline="")
    else:
        write_items_csv(folder / items_name, rows, columns, bom=bom)
    return path


# ---------------------------------------------------------------------------
# Study objects built directly (no files), for assign and leak tests
# ---------------------------------------------------------------------------


def scale_obj(d: dict[str, Any]) -> Scale:
    if d["type"] == "ordinal":
        values: tuple[Any, ...] = tuple(range(d["min"], d["max"] + 1))
    else:
        values = tuple(d["values"])
    return Scale(
        id=d["id"],
        label=d.get("label", d["id"]),
        type=d["type"],
        values=values,
        description=d.get("description", ""),
        required=d.get("required", True),
    )


def items_from_rows(rows: list[dict[str, str]]) -> tuple[Item, ...]:
    return tuple(
        Item(
            item_id=r["item_id"],
            source=r["source"],
            text=r["text"],
            case_id=r.get("case_id"),
            context=r.get("context"),
        )
        for r in rows
    )


def make_study(
    counts: dict[str, int] | None = None,
    *,
    rows: list[dict[str, str]] | None = None,
    raters: tuple[str, ...] = ("R1", "R2", "R3"),
    rpi: int | None = None,
    group_by_case: bool = False,
    scales: list[dict[str, Any]] | None = None,
    comments: bool = False,
    seed: int = SEED,
    with_case: bool = False,
    with_context: bool = False,
    cases_of: int = 2,
) -> Study:
    if rows is None:
        rows = make_item_rows(
            counts, with_case=with_case or group_by_case, with_context=with_context, cases_of=cases_of
        )
    return Study(
        title="Synthetic rating study",
        seed=seed,
        instructions="Rate each text on its own merits.",
        raters=tuple(raters),
        raters_per_item=len(raters) if rpi is None else rpi,
        group_by_case=group_by_case,
        scales=tuple(scale_obj(s) for s in (DEFAULT_SCALES if scales is None else scales)),
        comments=comments,
        items=items_from_rows(rows),
        ignored_columns=(),
    )


def study_from_texts(
    texts_by_source: dict[str, list[str]],
    *,
    contexts_by_source: dict[str, list[str | None]] | None = None,
) -> Study:
    """A study whose items have exactly the given texts (and optional contexts)."""
    rows: list[dict[str, str]] = []
    n = 0
    for src, texts in texts_by_source.items():
        for i, t in enumerate(texts):
            n += 1
            row: dict[str, Any] = {"item_id": f"it{n:04d}", "source": src, "text": t}
            ctxs = (contexts_by_source or {}).get(src)
            if ctxs is not None and ctxs[i] is not None:
                row["context"] = ctxs[i]
            rows.append(row)
    return make_study(rows=rows)


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


@dataclass
class Built:
    folder: Path
    study_path: Path
    study: Study
    out: Path
    result: Any
    key: dict
    manifest: dict


def make_built(
    tmp_path: Path,
    *,
    name: str = "proj",
    out: str = "out",
    nonce: str = NONCE,
    created: str = CREATED,
    **study_kwargs: Any,
) -> Built:
    study_path = write_study(tmp_path / name, **study_kwargs)
    study = load_study(study_path)
    out_dir = tmp_path / out
    result = build(study, out_dir, nonce=nonce, created=created)
    key = json.loads((out_dir / "private" / "key.json").read_text(encoding="utf-8"))
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    return Built(tmp_path / name, study_path, study, out_dir, result, key, manifest)


DEFAULT_BUILD_KWARGS: dict[str, Any] = dict(
    rows=make_item_rows({SRC_A: 7, SRC_B: 7}, with_case=True, with_context=True),
    raters=["R1", "R2", "R3"],
    rpi=2,
    comments=True,
)


@pytest.fixture
def built(tmp_path: Path) -> Built:
    """14 items, two sources, three raters, two raters per item, comments, context, case ids."""
    return make_built(tmp_path, **DEFAULT_BUILD_KWARGS)


# ---------------------------------------------------------------------------
# Acting as a rater (openpyxl only, using the _meta column map)
# ---------------------------------------------------------------------------


def read_meta(wb: openpyxl.Workbook) -> dict:
    return json.loads(wb["_meta"]["A1"].value)


def column_map(ws: Any, meta: dict) -> dict[str, int]:
    """role/scale_id -> 1-based column index, using the headers recorded in _meta."""
    headers = {ws.cell(1, c).value: c for c in range(1, ws.max_column + 1)}
    cmap: dict[str, int] = {}
    for col in meta["columns"]:
        key = col["scale_id"] if col["role"] == "scale" else col["role"]
        cmap[key] = headers[col["header"]]
    return cmap


def open_sheet(path: Path) -> tuple[openpyxl.Workbook, Any, dict, dict[str, int]]:
    wb = openpyxl.load_workbook(path)
    meta = read_meta(wb)
    ws = wb["Ratings"]
    return wb, ws, meta, column_map(ws, meta)


def data_row_numbers(ws: Any, cmap: dict[str, int]) -> list[int]:
    return [r for r in range(2, ws.max_row + 1) if ws.cell(r, cmap["rating_id"]).value not in (None, "")]


def default_value(scale: dict, rating_id: str) -> Any:
    h = int(hashlib.sha256(f"{rating_id}|{scale['id']}".encode()).hexdigest(), 16)
    return scale["values"][h % len(scale["values"])]


@dataclass
class Filled:
    dir: Path
    paths: dict[str, Path] = field(default_factory=dict)
    values: dict[str, dict[str, Any]] = field(default_factory=dict)  # rating_id -> scale_id -> value
    comments: dict[str, str] = field(default_factory=dict)  # rating_id -> comment


def fill_workbooks(
    out_dir: Path,
    dest: Path,
    *,
    value_fn: Callable[[dict, str], Any] | None = None,
    skip_raters: tuple[str, ...] = (),
    comments: bool = True,
) -> Filled:
    """Copy every built sheet into `dest`, filling it in as a rater would."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    filled = Filled(dest)
    for rater in manifest["raters"]:
        if rater in skip_raters:
            continue
        src = out_dir / manifest["sheets"][rater]["file"]
        wb, ws, meta, cmap = open_sheet(src)
        for i, r in enumerate(data_row_numbers(ws, cmap)):
            rid = ws.cell(r, cmap["rating_id"]).value
            filled.values[rid] = {}
            for sc in manifest["scales"]:
                v = (value_fn or default_value)(sc, rid)
                ws.cell(r, cmap[sc["id"]]).value = v
                filled.values[rid][sc["id"]] = v
            if comments and "comment" in cmap and i % 2 == 0:
                text = f"Synthetic comment for row {i + 1}"
                ws.cell(r, cmap["comment"]).value = text
                filled.comments[rid] = text
        target = dest / f"{rater}.xlsx"
        wb.save(target)
        filled.paths[rater] = target
    return filled


@pytest.fixture
def filled(built: Built, tmp_path: Path) -> Filled:
    return fill_workbooks(built.out, tmp_path / "returned")


def reorder_rows(path: Path, order: list[int]) -> None:
    """Rewrite the data rows of a returned sheet in a new order (a rater who sorted it)."""
    wb, ws, meta, cmap = open_sheet(path)
    rows = data_row_numbers(ws, cmap)
    snapshot = [
        [(ws.cell(r, c).value, ws.cell(r, c).data_type) for c in range(1, ws.max_column + 1)] for r in rows
    ]
    for new_i, old_i in enumerate(order):
        for c, (val, dt) in enumerate(snapshot[old_i], start=1):
            cell = ws.cell(rows[new_i], c)
            cell.value = val
            if dt == "s":
                cell.data_type = "s"
    wb.save(path)


def edit_cell(path: Path, row_index: int, key: str, value: Any, *, force_string: bool = False) -> None:
    """Set the cell for `key` (rating_id/text/context/comment/scale id) in data row `row_index` (0-based)."""
    wb, ws, meta, cmap = open_sheet(path)
    r = data_row_numbers(ws, cmap)[row_index]
    cell = ws.cell(r, cmap[key])
    cell.value = value
    if force_string and isinstance(value, str):
        cell.data_type = "s"
    wb.save(path)


def get_cell(path: Path, row_index: int, key: str) -> Any:
    wb, ws, meta, cmap = open_sheet(path)
    r = data_row_numbers(ws, cmap)[row_index]
    return ws.cell(r, cmap[key]).value


def clear_row(path: Path, row_index: int) -> str:
    """Blank every cell of one data row; return the rating id it had."""
    wb, ws, meta, cmap = open_sheet(path)
    r = data_row_numbers(ws, cmap)[row_index]
    rid = ws.cell(r, cmap["rating_id"]).value
    for c in range(1, ws.max_column + 1):
        ws.cell(r, c).value = None
    wb.save(path)
    return rid


def edit_meta(path: Path, **changes: Any) -> None:
    wb = openpyxl.load_workbook(path)
    meta = read_meta(wb)
    meta.update(changes)
    wb["_meta"]["A1"].value = json.dumps(meta)
    wb.save(path)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sheet_strings(path: Path) -> list[str]:
    """Every string cell value of every sheet of a workbook."""
    wb = openpyxl.load_workbook(path)
    out: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str):
                    out.append(c.value)
    return out


def zip_text(path: Path) -> str:
    """All parts of an xlsx (a zip) joined, decoded loosely, for substring searches."""
    parts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            parts.append(zf.read(name).decode("utf-8", "ignore"))
    return "\n".join(parts)


def codes(result: Any) -> list[str]:
    return [p.code for p in result.problems]


def problems_with(result: Any, code: str) -> list[Any]:
    return [p for p in result.problems if p.code == code]


def expected_scale_count(manifest: dict) -> int:
    return sum(1 for s in manifest["scales"] if s["required"])
