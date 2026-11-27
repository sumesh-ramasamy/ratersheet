"""build: output layout, manifest, key.json, commitment, overwrite rules, determinism."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

import ratersheet
from ratersheet import (
    BuildResult,
    LeakWarning,
    RatersheetError,
    build,
    check_leaks,
    file_sha256,
    leak_coverage,
    load_study,
    make_design,
    read_workbook,
    text_sha256,
)
from conftest import (
    CREATED,
    DEFAULT_BUILD_KWARGS,
    NONCE,
    SEED,
    SRC_A,
    SRC_B,
    Built,
    fill_workbooks,
    make_built,
    make_item_rows,
    sheet_strings,
    study_from_texts,
    write_study,
    zip_text,
)

TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def dumps(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---- layout ---------------------------------------------------------------------------------


def test_output_layout(built: Built) -> None:
    out = built.out
    assert (out / "manifest.json").is_file()
    for r in ("R1", "R2", "R3"):
        assert (out / "sheets" / f"{r}.xlsx").is_file()
    for name in ("key.json", "leak_report.json", "README.txt"):
        assert (out / "private" / name).is_file(), name
    assert "rater" in (out / "private" / "README.txt").read_text(encoding="utf-8").lower()


def test_build_result(built: Built) -> None:
    res = built.result
    assert isinstance(res, BuildResult)
    assert Path(res.out_dir) == built.out
    assert set(res.sheets) == {"R1", "R2", "R3"}
    for r, p in res.sheets.items():
        assert Path(p) == built.out / "sheets" / f"{r}.xlsx"
    assert Path(res.key_path) == built.out / "private" / "key.json"
    assert Path(res.manifest_path) == built.out / "manifest.json"
    assert res.key_sha256 == file_sha256(res.key_path)
    assert isinstance(res.warnings, list)


def test_out_dir_may_be_a_str_and_may_already_exist_empty(tmp_path: Path) -> None:
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    out = tmp_path / "pre-existing"
    out.mkdir()
    res = build(study, str(out), nonce=NONCE, created=CREATED)
    assert (out / "manifest.json").is_file() and res.key_path.is_file()


def test_files_are_utf8_with_lf_newlines_and_canonical_json(built: Built) -> None:
    for p in (built.out / "manifest.json", built.out / "private" / "key.json", built.out / "private" / "leak_report.json"):
        raw = p.read_bytes()
        assert b"\r" not in raw, p.name
        text = raw.decode("utf-8")
        assert text.endswith("\n") and not text.endswith("\n\n")
        assert text == dumps(json.loads(text)), f"{p.name} is not written the canonical way"


# ---- key.json -------------------------------------------------------------------------------


def test_key_structure(built: Built) -> None:
    key = built.key
    assert set(key) == {
        "format_version", "tool_version", "created", "title", "seed", "nonce", "sources", "items", "assignments",
    }
    assert key["format_version"] == 1
    assert key["tool_version"] == ratersheet.__version__
    assert key["created"] == CREATED
    assert key["title"] == built.study.title
    assert key["seed"] == SEED
    assert key["nonce"] == NONCE
    assert key["sources"] == [SRC_A, SRC_B]
    assert len(key["items"]) == 14 and len(key["assignments"]) == 28


def test_key_items(built: Built) -> None:
    design = make_design(built.study, secret=NONCE)
    by_id = {i["item_id"]: i for i in built.key["items"]}
    assert set(by_id) == {i.item_id for i in built.study.items}
    for it in built.study.items:
        k = by_id[it.item_id]
        assert set(k) == {"item_id", "item_code", "source", "case_id", "text_sha256", "context_sha256"}
        assert k["source"] == it.source and k["case_id"] == it.case_id
        assert k["item_code"] == design.item_codes[it.item_id]
        assert k["text_sha256"] == text_sha256(it.text)
        assert k["context_sha256"] == (text_sha256(it.context) if it.context is not None else None)


def test_key_context_hash_is_null_without_context(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 3, SRC_B: 3}))
    assert all(i["context_sha256"] is None and i["case_id"] is None for i in b.key["items"])


def test_key_assignments_match_the_design(built: Built) -> None:
    design = make_design(built.study, secret=NONCE)
    assert built.result.design == design, "BuildResult carries the design that was built"
    expected = [
        {"rating_id": a.rating_id, "rater_id": a.rater_id, "item_id": a.item_id, "position": a.position}
        for a in design.assignments
    ]
    got = sorted(built.key["assignments"], key=lambda a: (a["rater_id"], a["position"]))
    assert sorted(expected, key=lambda a: (a["rater_id"], a["position"])) == got
    for a in built.key["assignments"]:
        assert set(a) == {"rating_id", "rater_id", "item_id", "position"}


def test_default_nonce_is_random_64_hex_and_created_is_a_timestamp(tmp_path: Path) -> None:
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    build(study, tmp_path / "o1")
    build(study, tmp_path / "o2")
    k1 = json.loads((tmp_path / "o1" / "private" / "key.json").read_text(encoding="utf-8"))
    k2 = json.loads((tmp_path / "o2" / "private" / "key.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{64}", k1["nonce"]) and re.fullmatch(r"[0-9a-f]{64}", k2["nonce"])
    assert k1["nonce"] != k2["nonce"]
    assert TS_RE.match(k1["created"])
    # different commitments: the nonce makes the commitment hiding
    assert file_sha256(tmp_path / "o1" / "private" / "key.json") != file_sha256(tmp_path / "o2" / "private" / "key.json")
    # and different designs: the ids, the codes and the order are drawn from the nonce, so two
    # builds of one study share none of them and the seed alone predicts none of them
    assert not {a["rating_id"] for a in k1["assignments"]} & {a["rating_id"] for a in k2["assignments"]}
    assert not {i["item_code"] for i in k1["items"]} & {i["item_code"] for i in k2["items"]}
    assert [(a["rater_id"], a["item_id"]) for a in k1["assignments"]] != [
        (a["rater_id"], a["item_id"]) for a in k2["assignments"]
    ]


def test_first_rating_id_of_a_build_is_not_the_one_the_seed_alone_gives(built: Built) -> None:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    seed_only = ratersheet.DeterministicRNG(SEED, "ids")
    seed_only_codes = ["I-" + seed_only.code(alphabet, 6) for _ in built.study.items]
    seed_only_first_id = seed_only.code(alphabet, 4) + "-" + seed_only.code(alphabet, 4)
    first = min(built.key["assignments"], key=lambda a: (a["rater_id"], a["position"]))
    assert (first["rater_id"], first["position"]) == ("R1", 1)
    assert first["rating_id"] != seed_only_first_id
    assert not {i["item_code"] for i in built.key["items"]} & set(seed_only_codes)
    assert first["rating_id"] == read_workbook(built.out / "sheets" / "R1.xlsx").rows[0].rating_id


def test_design_is_reproducible_from_the_key_alone(tmp_path: Path) -> None:
    # An upper-case nonce is accepted; the key records the spelling that was used as the secret.
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    res = build(study, tmp_path / "o", nonce="ABCDEF0123456789" * 4, created=CREATED)
    key = json.loads(res.key_path.read_text(encoding="utf-8"))
    assert key["nonce"] == "abcdef0123456789" * 4
    assert key["seed"] == study.seed
    design = make_design(study, secret=key["nonce"])
    assert design == res.design
    assert [a["rating_id"] for a in key["assignments"]] == [a.rating_id for a in design.assignments]
    assert {i["item_id"]: i["item_code"] for i in key["items"]} == design.item_codes


def test_fixed_nonce_and_created_give_byte_identical_key(tmp_path: Path) -> None:
    a = make_built(tmp_path, name="p1", out="o1", **DEFAULT_BUILD_KWARGS)
    b = make_built(tmp_path, name="p2", out="o2", **DEFAULT_BUILD_KWARGS)
    assert (a.out / "private" / "key.json").read_bytes() == (b.out / "private" / "key.json").read_bytes()
    assert a.result.key_sha256 == b.result.key_sha256


def test_different_nonce_changes_the_commitment(tmp_path: Path) -> None:
    a = make_built(tmp_path, name="p1", out="o1", **DEFAULT_BUILD_KWARGS)
    b = make_built(tmp_path, name="p2", out="o2", nonce="f" * 64, **DEFAULT_BUILD_KWARGS)
    assert a.result.key_sha256 != b.result.key_sha256


# ---- commitment -----------------------------------------------------------------------------


def test_key_sha256_is_the_file_hash_and_is_everywhere(built: Built) -> None:
    h = file_sha256(built.out / "private" / "key.json")
    assert built.result.key_sha256 == h
    assert built.manifest["key_sha256"] == h
    for r in built.manifest["raters"]:
        path = built.out / "sheets" / f"{r}.xlsx"
        rb = read_workbook(path)
        assert rb.meta["key_sha256"] == h, f"{r}: _meta commitment"
        assert f"Key commitment (SHA-256): {h}" in sheet_strings(path), f"{r}: Instructions line"


# ---- manifest -------------------------------------------------------------------------------


def test_manifest_structure_and_values(built: Built) -> None:
    m = built.manifest
    assert set(m) == {
        "format_version", "tool", "tool_version", "created", "title", "n_items", "n_sources", "raters",
        "raters_per_item", "group_by_case", "comments", "has_context", "scales", "per_rater", "n_assignments",
        "key_sha256", "sheets",
    }
    assert m["format_version"] == 1 and m["tool"] == "ratersheet" and m["tool_version"] == ratersheet.__version__
    assert m["created"] == CREATED and m["title"] == built.study.title
    assert m["n_items"] == 14 and m["n_sources"] == 2
    assert m["raters"] == ["R1", "R2", "R3"] and m["raters_per_item"] == 2
    assert m["group_by_case"] is False and m["comments"] is True and m["has_context"] is True
    assert m["n_assignments"] == 28
    assert sum(m["per_rater"].values()) == 28 and set(m["per_rater"]) == {"R1", "R2", "R3"}
    assert max(m["per_rater"].values()) - min(m["per_rater"].values()) <= 1
    assert m["scales"] == [
        {"id": "accuracy", "label": "Accuracy", "type": "ordinal", "values": [1, 2, 3, 4, 5],
         "description": "1 = wrong, 5 = fully correct", "required": True},
        {"id": "harm", "label": "harm", "type": "nominal", "values": ["none", "minor", "serious"],
         "description": "", "required": True},
    ]


def test_manifest_sheet_hashes(built: Built) -> None:
    for r, entry in built.manifest["sheets"].items():
        assert entry["file"] == f"sheets/{r}.xlsx"
        assert entry["sha256"] == file_sha256(built.out / entry["file"])


def test_manifest_has_context_false_without_context(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 3, SRC_B: 3}))
    assert b.manifest["has_context"] is False and b.manifest["comments"] is False


def test_manifest_contains_no_source_item_case_or_seed(built: Built) -> None:
    text = (built.out / "manifest.json").read_text(encoding="utf-8")
    needles = [i.source for i in built.study.items] + [i.item_id for i in built.study.items]
    needles += [i.case_id for i in built.study.items if i.case_id] + [str(SEED)]
    for n in set(needles):
        assert n not in text, f"manifest leaks {n!r}"


def test_sheets_contain_no_source_item_id_case_id_or_seed(built: Built) -> None:
    needles = {i.source for i in built.study.items} | {i.item_id for i in built.study.items}
    needles |= {i.case_id for i in built.study.items if i.case_id} | {str(SEED)}
    for r in built.manifest["raters"]:
        blob = zip_text(built.out / "sheets" / f"{r}.xlsx")
        for n in needles:
            assert n not in blob, f"sheet {r} contains {n!r}"


# ---- the sheets -------------------------------------------------------------------------------


def test_sheets_match_the_key(built: Built) -> None:
    items = {i.item_id: i for i in built.study.items}
    for r in built.manifest["raters"]:
        mine = sorted((a for a in built.key["assignments"] if a["rater_id"] == r), key=lambda a: a["position"])
        rb = read_workbook(built.out / "sheets" / f"{r}.xlsx")
        assert rb.meta["rater_id"] == r
        assert [x.rating_id for x in rb.rows] == [a["rating_id"] for a in mine], f"{r}: order of rows"
        for row, a in zip(rb.rows, mine):
            it = items[a["item_id"]]
            assert row.text == it.text and row.context == it.context, f"{r} {a['rating_id']}"
        assert len(rb.rows) == built.manifest["per_rater"][r]


def test_group_by_case_flag_reaches_the_manifest(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 4}, with_case=True), group_by_case=True, rpi=2)
    assert b.manifest["group_by_case"] is True
    case_of = {i["item_id"]: i["case_id"] for i in b.key["items"]}
    for r in b.manifest["raters"]:
        seq = [case_of[a["item_id"]] for a in sorted(
            (x for x in b.key["assignments"] if x["rater_id"] == r), key=lambda x: x["position"])]
        collapsed = [c for i, c in enumerate(seq) if i == 0 or seq[i - 1] != c]
        assert len(collapsed) == len(set(collapsed))


# ---- leak warnings ----------------------------------------------------------------------------


def leaky_rows() -> list[dict[str, str]]:
    rows = make_item_rows({SRC_A: 5, SRC_B: 5})
    rows[0]["text"] = "As an AI language model I would say this " + rows[0]["text"]
    return rows


def test_leak_warnings_are_reported_everywhere(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=leaky_rows())
    expected = check_leaks(b.study)
    assert expected, "the constructed study should leak"
    assert all(isinstance(w, LeakWarning) for w in b.result.warnings)
    assert sorted((w.code, tuple(w.item_ids)) for w in b.result.warnings) == sorted(
        (w.code, tuple(w.item_ids)) for w in expected
    )
    report_text = (b.out / "private" / "leak_report.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert sorted((w["code"], tuple(w["item_ids"])) for w in report["warnings"]) == sorted(
        (w.code, tuple(w.item_ids)) for w in expected
    )
    assert b.study.items[0].item_id in report_text, "leak_report.json carries item ids"
    # the manifest says nothing about the warnings, not even their kinds
    mtext = (b.out / "manifest.json").read_text(encoding="utf-8")
    assert b.study.items[0].item_id not in mtext
    assert "leak" not in mtext
    for w in expected:
        assert w.code not in mtext, f"the manifest names the leak cue {w.code}"


def test_clean_study_has_no_warnings(built: Built) -> None:
    assert built.result.warnings == []
    assert "leak_warnings" not in built.manifest
    report = json.loads((built.out / "private" / "leak_report.json").read_text(encoding="utf-8"))
    assert report["warnings"] == []


def test_manifest_is_the_same_whether_or_not_a_cue_separates_the_sources(tmp_path: Path) -> None:
    # The raters of both studies see the same twelve texts. In the first, one source wrote every
    # long, bulleted text; in the second the sources are mixed. Nothing in the manifest, which
    # anyone may read, may tell the two apart: that would say which cue separates the sources.
    long_texts = [f"- point one about topic {n}\n- point two, with a good deal more detail\n- point three" for n in range(6)]
    short_texts = [f"Short answer {n}." for n in range(6)]
    separable = study_from_texts({SRC_A: long_texts, SRC_B: short_texts})
    mixed = study_from_texts({SRC_A: long_texts[:3] + short_texts[:3], SRC_B: long_texts[3:] + short_texts[3:]})
    assert {w.code for w in check_leaks(separable)} == {"length_separable", "formatting_separable"}
    assert check_leaks(mixed) == []
    manifests = []
    for name, study in (("separable", separable), ("mixed", mixed)):
        res = build(study, tmp_path / name, nonce=NONCE, created=CREATED)
        text = res.manifest_path.read_text(encoding="utf-8")
        assert "separable" not in text and "leak" not in text
        manifest = json.loads(text)
        del manifest["key_sha256"], manifest["sheets"]  # hashes of files that differ
        manifests.append(manifest)
    assert manifests[0] == manifests[1]


def test_leak_report_says_what_the_checks_could_look_at(tmp_path: Path) -> None:
    # Three items per source: too few for the length and markup comparison, which must be on
    # record beside "no warnings".
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 3, SRC_B: 3}))
    report = json.loads((b.out / "private" / "leak_report.json").read_text(encoding="utf-8"))
    coverage = leak_coverage(b.study)
    assert report["coverage"] == json.loads(json.dumps(dataclasses.asdict(coverage)))
    assert report["coverage"]["small_sources"] == [SRC_A, SRC_B]
    assert report["coverage"]["n_pairs"] == 1 and report["coverage"]["n_pairs_compared"] == 0
    assert report["warnings"] == []


# ---- permissions ------------------------------------------------------------------------------


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no owner/group/other permission bits")
def test_private_folder_and_key_are_readable_by_the_owner_only(tmp_path: Path) -> None:
    old_umask = os.umask(0o022)  # the usual default, under which a plain write is world-readable
    try:
        b = make_built(tmp_path, rows=leaky_rows())
        private = b.out / "private"
        assert mode(private) == 0o700
        for name in ("key.json", "leak_report.json"):
            assert mode(private / name) == 0o600, name
        assert mode(b.out / "manifest.json") & 0o044, "the manifest stays readable by others"
        # an overwrite build tightens a folder that was opened up, and the new files with it
        os.chmod(private, 0o755)
        build(b.study, b.out, nonce="3" * 64, created=CREATED, overwrite=True)
        assert mode(private) == 0o700
        assert mode(private / "key.json") == 0o600
    finally:
        os.umask(old_umask)
    assert b.result.key_sha256 == b.manifest["key_sha256"], "the commitment is of the bytes written"


# ---- overwrite --------------------------------------------------------------------------------


def test_build_refuses_to_overwrite(built: Built) -> None:
    before = (built.out / "private" / "key.json").read_bytes()
    with pytest.raises(RatersheetError):
        build(built.study, built.out, nonce="1" * 64, created=CREATED)
    assert (built.out / "private" / "key.json").read_bytes() == before, "a refused build must not touch the key"


REFUSED_WHEN_ALONE = [
    "private/key.json",
    "private/leak_report.json",
    "private/README.txt",
    "sheets/R1.xlsx",
    "sheets/budget-2026.xlsx",
    "sheets/returned by a rater.XLSX",
    "methods.md",
    "ratings.csv",
    "comments.csv",
    "ratings_blinded.csv",
    "comments_blinded.csv",
    "collect_report.json",
    "unblind_record.json",
]


@pytest.mark.parametrize("existing", REFUSED_WHEN_ALONE)
def test_build_refuses_to_replace_any_existing_file_without_overwrite(tmp_path: Path, existing: str) -> None:
    # No manifest.json: the folder is not a study folder, but one file a build would delete or
    # replace is there (the user's own, or the key of a study whose manifest was moved away).
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    out = tmp_path / "o"
    (out / existing).parent.mkdir(parents=True)
    (out / existing).write_bytes(b"precious")
    (out / "notes.txt").write_bytes(b"unrelated")
    with pytest.raises(RatersheetError) as excinfo:
        build(study, out, nonce=NONCE, created=CREATED)
    message = str(excinfo.value)
    assert str(out / existing) in message, "the message names the file that is in the way"
    assert "overwrite" in message
    assert sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()) == sorted(
        [existing, "notes.txt"]
    ), "a refused build must not create or delete anything"
    assert (out / existing).read_bytes() == b"precious"
    # with overwrite the build goes ahead and the file is replaced or removed
    res = build(study, out, nonce=NONCE, created=CREATED, overwrite=True)
    assert res.manifest_path.is_file()
    assert not (out / existing).exists() or (out / existing).read_bytes() != b"precious"
    assert (out / "notes.txt").read_bytes() == b"unrelated"


def test_build_without_the_manifest_does_not_replace_the_key_of_a_live_study(built: Built, tmp_path: Path) -> None:
    key_file = built.out / "private" / "key.json"
    before = {p: p.read_bytes() for p in built.out.rglob("*") if p.is_file()}
    (built.out / "manifest.json").rename(tmp_path / "manifest-shared.json")  # "safe to share", so it travels
    with pytest.raises(RatersheetError) as excinfo:
        build(built.study, built.out, nonce="1" * 64, created=CREATED)
    assert str(key_file) in str(excinfo.value), "the message names the key that would be lost"
    assert "other files" in str(excinfo.value), "the message says that more files are in the way"
    (tmp_path / "manifest-shared.json").rename(built.out / "manifest.json")
    assert {p: p.read_bytes() for p in built.out.rglob("*") if p.is_file()} == before
    assert file_sha256(key_file) == built.manifest["key_sha256"], "the sheets already sent can still be collected"


def test_build_into_a_folder_with_unrelated_files_is_fine(tmp_path: Path) -> None:
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    out = tmp_path / "o"
    for name in ("analysis.R", "sheets/notes.txt", "private/passwords.txt", "sheets/sub/a.xlsx"):
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(b"mine")
    res = build(study, out, nonce=NONCE, created=CREATED)
    assert res.manifest_path.is_file()
    for name in ("analysis.R", "sheets/notes.txt", "private/passwords.txt", "sheets/sub/a.xlsx"):
        assert (out / name).read_bytes() == b"mine", name


def test_overwrite_flag_rebuilds_with_new_key(built: Built) -> None:
    old = built.result.key_sha256
    res = build(built.study, built.out, nonce="1" * 64, created=CREATED, overwrite=True)
    assert res.key_sha256 != old
    assert json.loads((built.out / "manifest.json").read_text(encoding="utf-8"))["key_sha256"] == res.key_sha256
    for r in res.sheets:
        assert read_workbook(res.sheets[r]).meta["key_sha256"] == res.key_sha256


def test_overwrite_removes_previous_collect_and_unblind_outputs(built: Built, tmp_path: Path) -> None:
    from ratersheet import collect, unblind

    fill_workbooks(built.out, tmp_path / "ret")
    res = collect(built.out, tmp_path / "ret")
    assert res.ok
    unblind(built.out)
    leftovers = ["collect_report.json", "ratings_blinded.csv", "comments_blinded.csv", "ratings.csv",
                 "comments.csv", "unblind_record.json"]
    for name in leftovers:
        assert (built.out / name).exists(), name
    build(built.study, built.out, nonce="2" * 64, created=CREATED, overwrite=True)
    for name in leftovers:
        assert not (built.out / name).exists(), f"{name} survived an overwrite build"


def test_overwrite_removes_sheets_of_raters_no_longer_in_the_study(tmp_path: Path) -> None:
    b = make_built(tmp_path, rows=make_item_rows({SRC_A: 4, SRC_B: 4}), raters=["R1", "R2", "R3"])
    assert (b.out / "sheets" / "R3.xlsx").exists()
    study2 = load_study(write_study(tmp_path / "p2", rows=make_item_rows({SRC_A: 4, SRC_B: 4}), raters=["R1", "R2"]))
    build(study2, b.out, nonce=NONCE, created=CREATED, overwrite=True)
    assert not (b.out / "sheets" / "R3.xlsx").exists()
    assert (b.out / "sheets" / "R1.xlsx").exists()


def test_overwrite_on_a_fresh_folder_is_fine(tmp_path: Path) -> None:
    study = load_study(write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS))
    res = build(study, tmp_path / "fresh", nonce=NONCE, created=CREATED, overwrite=True)
    assert res.manifest_path.is_file()


def test_build_does_not_modify_the_study_files(tmp_path: Path) -> None:
    p = write_study(tmp_path / "p", **DEFAULT_BUILD_KWARGS)
    before = {f.name: f.read_bytes() for f in p.parent.iterdir()}
    build(load_study(p), tmp_path / "o", nonce=NONCE, created=CREATED)
    assert before == {f.name: f.read_bytes() for f in p.parent.iterdir()}
