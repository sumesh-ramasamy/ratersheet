"""The ``ratersheet`` command line.

Exit codes: 0 for success; 1 when the check a command performs fails
(``collect`` found errors, ``--strict`` found leak warnings, ``verify`` found
a mismatch); 2 when the command could not run (bad usage, or anything the
tool refuses on purpose) or, for ``verify``, could not read one of the sheets
it was given. Deliberate failures print one ``error:`` line and never a
traceback.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from ._example import ITEMS_CSV, ITEMS_FILE_NAME, STUDY_FILE_NAME, STUDY_TOML
from ._store import (
    LEAK_REPORT_NAME,
    MANIFEST_NAME,
    METHODS_NAME,
    PRIVATE_DIR,
    RATINGS_NAME,
    SHEETS_DIR,
    write_text,
)
from .assign import Design, design_summary
from .build import build
from .collect import CollectResult, collect
from .config import Study, load_study
from .errors import IntegrityError, RatersheetError
from .leaks import LeakWarning, check_leaks, coverage_notes, leak_coverage
from .methods import write_methods
from .unblind import unblind, verify_key, verify_sheet

DEFAULT_OUT_DIR_NAME = "ratersheet-out"
_MAX_ITEMS_LISTED = 10


def main(argv: list[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    _escape_unencodable_output()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits by itself for --help and --version (0) and for bad usage (2).
        return exc.code if isinstance(exc.code, int) else 2
    if args.handler is None:
        parser.print_usage(sys.stderr)
        print("error: no command given; see 'ratersheet --help'", file=sys.stderr)
        return 2
    try:
        return args.handler(args)
    except RatersheetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # For example a full disk or a folder without write permission.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyError, TypeError) as exc:
        # The files of a study folder are checked for the entries the tool
        # needs, not for the shape of every value, so a hand-edited manifest or
        # record can still end here. Commands that read no study folder are
        # left alone: there, the same exception is a bug and should look like one.
        if not hasattr(args, "study_dir"):
            raise
        print(
            "error: a file in the study folder does not have the expected content "
            f"({type(exc).__name__}: {exc}); it may have been edited",
            file=sys.stderr,
        )
        return 2


def _escape_unencodable_output() -> None:
    """Make stdout write an escape for a character it cannot encode.

    A title, a source label or a path can hold characters that the encoding of
    stdout lacks (output redirected on Windows, a non-UTF-8 locale). Printing
    one would otherwise end the command with a traceback and exit code 1,
    possibly after all its files were written. The encoding itself is kept, so
    that redirected output stays readable by other programs.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)  # absent when stdout was replaced
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ratersheet",
        description="Blinded, balanced rating sheets for human evaluation of texts.",
    )
    parser.add_argument("--version", action="version", version=f"ratersheet {__version__}")
    parser.set_defaults(handler=None)
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")

    init = commands.add_parser("init", help="write an example study.toml and items.csv")
    init.add_argument("dir", nargs="?", default=".", metavar="DIR", help="folder (default: .)")
    init.set_defaults(handler=_run_init)

    check = commands.add_parser(
        "check", help="show the load per rater and the leak warnings; write nothing"
    )
    check.add_argument("study", metavar="STUDY.toml")
    check.add_argument("--strict", action="store_true", help="exit 1 if there are leak warnings")
    check.set_defaults(handler=_run_check)

    build_cmd = commands.add_parser("build", help="write the sheets, the key and the manifest")
    build_cmd.add_argument("study", metavar="STUDY.toml")
    build_cmd.add_argument(
        "--out",
        metavar="DIR",
        help=f"output folder (default: {DEFAULT_OUT_DIR_NAME} next to the study file)",
    )
    build_cmd.add_argument(
        "--strict", action="store_true", help="write nothing and exit 1 if there are leak warnings"
    )
    build_cmd.add_argument(
        "--overwrite",
        action="store_true",
        help="first delete what an earlier build, collect, unblind or methods wrote in the "
        "folder, by file name, and every .xlsx file in its sheets/ folder; the earlier key is "
        "lost",
    )
    build_cmd.set_defaults(handler=_run_build)

    collect_cmd = commands.add_parser("collect", help="validate returned workbooks")
    collect_cmd.add_argument("study_dir", metavar="STUDY_DIR")
    collect_cmd.add_argument(
        "returned", nargs="+", metavar="RETURNED", help="returned .xlsx files or folders of them"
    )
    collect_cmd.add_argument(
        "--require-complete",
        action="store_true",
        help="treat missing workbooks and missing required ratings as errors",
    )
    collect_cmd.set_defaults(handler=_run_collect)

    verify = commands.add_parser("verify", help="check the key and sheets against the manifest")
    verify.add_argument("study_dir", metavar="STUDY_DIR")
    verify.add_argument("sheets", nargs="*", metavar="SHEET.xlsx")
    verify.set_defaults(handler=_run_verify)

    unblind_cmd = commands.add_parser("unblind", help="write ratings.csv with sources")
    unblind_cmd.add_argument("study_dir", metavar="STUDY_DIR")
    unblind_cmd.add_argument(
        "--allow-incomplete", action="store_true", help="unblind although ratings are missing"
    )
    unblind_cmd.set_defaults(handler=_run_unblind)

    methods = commands.add_parser("methods", help="write methods.md")
    methods.add_argument("study_dir", metavar="STUDY_DIR")
    methods.set_defaults(handler=_run_methods)
    return parser


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _listed(names: Sequence[str]) -> str:
    """Join names as a sentence does: "a", "a and b", "a, b and c"."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _print_leak_findings(study: Study, warnings: list[LeakWarning]) -> None:
    """Print the leak warnings, then what the checks could not look at.

    The second part keeps "none" honest: a check that was skipped found
    nothing. It is information, not a warning, and never fails ``--strict``.
    """
    if warnings:
        print(f"Leak warnings: {len(warnings)}")
    else:
        print("Leak warnings: none")
    for warning in warnings:
        print(f"  [{warning.code}] {warning.message}")
        if warning.item_ids:
            listed = ", ".join(warning.item_ids[:_MAX_ITEMS_LISTED])
            more = len(warning.item_ids) - _MAX_ITEMS_LISTED
            print(f"      items: {listed}" + (f" and {more} more" if more > 0 else ""))
    notes = coverage_notes(leak_coverage(study))
    if notes:
        print("Not checked:")
        for note in notes:
            print(f"  {note}")


def _even_share(n_assignments: int, n_raters: int) -> str:
    """Say how many assignments one rater gets when no two raters differ by more than one."""
    low, rest = divmod(n_assignments, n_raters)
    return str(low) if rest == 0 else f"{low} or {low + 1}"


def _print_plan(study: Study) -> None:
    """Print what is certain about the design before it is drawn.

    Who rates what is drawn at build time, so only the numbers that the
    balance guarantees fix can be shown: every rater's load, overall and per
    source, is an even share of the assignments.
    """
    per_item, n_raters = study.raters_per_item, len(study.raters)
    print(f"Study: {study.title}")
    print(
        f"{_plural(len(study.items), 'item')} from {_plural(len(study.sources), 'source')}; "
        f"{_plural(n_raters, 'rater')}; {per_item} per item; "
        f"{_plural(len(study.items) * per_item, 'assignment')}"
    )
    print(f"Raters: {', '.join(study.raters)}")
    print(f"Items per rater: {_even_share(len(study.items) * per_item, n_raters)}")
    print("Items per rater from each source:")
    for source in study.sources:
        n_items = sum(1 for item in study.items if item.source == source)
        print(f"  {source}: {_even_share(n_items * per_item, n_raters)}")
    print(
        "Who rates what, the order of each sheet and the codes are drawn at build time from a "
        "random secret kept in the key; they cannot be predicted from the study file."
    )
    if study.ignored_columns:
        print(f"Ignored columns of the items file: {', '.join(study.ignored_columns)}")


def _print_design(study: Study, design: Design) -> None:
    """Print each rater's load and each pair's shared items in a design that was built."""
    summary = design_summary(study, design)
    print("Items per rater (and per source):")
    for rater_id, n_items in summary["per_rater"].items():
        by_source = ", ".join(
            f"{source}: {n}" for source, n in summary["per_rater_source"][rater_id].items()
        )
        print(f"  {rater_id}: {n_items} ({by_source})")
    if not summary["pair_counts"]:
        return
    print("Items rated by both raters of a pair:")
    for pair, n_items in summary["pair_counts"].items():
        print(f"  {pair}: {n_items}")
    n_apart = sum(1 for n_items in summary["pair_counts"].values() if n_items == 0)
    if n_apart and study.raters_per_item > 1:
        print(
            f"  Note: {_plural(n_apart, 'pair')} of raters share no item, so agreement between "
            "them cannot be estimated."
        )


def _run_init(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    files = {folder / STUDY_FILE_NAME: STUDY_TOML, folder / ITEMS_FILE_NAME: ITEMS_CSV}
    for path in files:
        if path.exists():
            raise RatersheetError(f"{path} already exists; init does not overwrite files")
    folder.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        write_text(path, content)
        print(f"wrote {path}")
    print(f"Next: ratersheet check {folder / STUDY_FILE_NAME}")
    return 0


def _run_check(args: argparse.Namespace) -> int:
    study = load_study(args.study)
    warnings = check_leaks(study)
    _print_plan(study)
    _print_leak_findings(study, warnings)
    return 1 if args.strict and warnings else 0


def _run_build(args: argparse.Namespace) -> int:
    study_path = Path(args.study)
    study = load_study(study_path)
    if args.strict:
        warnings = check_leaks(study)
        if warnings:
            _print_leak_findings(study, warnings)
            print("Nothing was written because --strict was given.", file=sys.stderr)
            return 1
    out_dir = Path(args.out) if args.out else study_path.parent / DEFAULT_OUT_DIR_NAME
    result = build(study, out_dir, overwrite=args.overwrite)
    print(f"Built {_plural(len(result.sheets), 'rating sheet')} in {result.out_dir}")
    _print_design(study, result.design)
    if study.raters_per_item < len(study.raters) and len(study.sources) > 1:
        print(
            "Note: not every rater rates every item. Because each rater gets an equal share of "
            "every source, the pattern of who rated what narrows down which items share a "
            "source, strongly in a small study. Anyone who can see several sheets, or the "
            "blinded ratings, can use it. A fully crossed design (every rater rates every item) "
            "has no such pattern."
        )
    with_items = {assignment.rater_id for assignment in result.design.assignments}
    print("Send to the raters, one file each:")
    for rater_id, path in result.sheets.items():
        if rater_id in with_items:
            print(f"  {rater_id}: {path}")
    without_items = [rater_id for rater_id in study.raters if rater_id not in with_items]
    if without_items:
        # More raters than assignments: a sheet with no row would only puzzle its rater.
        sheets = "their sheets are" if len(without_items) > 1 else "that sheet is"
        print(
            f"No items were assigned to {_listed(without_items)}; {sheets} empty and need not "
            "be sent."
        )
    print("Keep private; never send to raters:")
    print(f"  {result.out_dir / PRIVATE_DIR}  (the unblinding key and the leak report)")
    print("Safe to share:")
    print(f"  {result.manifest_path}  (no sources, item ids or seed)")
    print(f"Key commitment (SHA-256): {result.key_sha256}")
    _print_leak_findings(study, result.warnings)
    if result.warnings:
        print(f"Details of the leak warnings: {result.out_dir / PRIVATE_DIR / LEAK_REPORT_NAME}")
    return 0


def _run_collect(args: argparse.Namespace) -> int:
    result = collect(args.study_dir, args.returned, require_complete=args.require_complete)
    for problem in result.problems:
        print(f"{problem.severity.upper():<7} {problem.code}: {problem.message}")
    n_errors = sum(1 for problem in result.problems if problem.severity == "error")
    if result.complete:
        state = "complete"
    elif result.n_received == result.n_expected:
        # Every required rating that could be counted is in, yet a rater's
        # sheet is outstanding (a study with no required scale, for example).
        state = "incomplete: at least one rater's sheet has not been returned"
    else:
        state = "incomplete"
    print(
        f"Received {result.n_received} of {_plural(result.n_expected, 'expected rating')} "
        f"({state})."
    )
    print(f"Report: {result.report_path}")
    if result.ok:
        print(f"OK: wrote {result.ratings_path}")
        if result.comments_path is not None:
            print(f"OK: wrote {result.comments_path}")
    else:
        print(f"FAILED: {_plural(n_errors, 'error')}; no dataset was written.")
    _print_what_collect_changed(Path(args.study_dir), result)
    return 0 if result.ok else 1


def _print_what_collect_changed(study_dir: Path, result: CollectResult) -> None:
    """Say what a ``collect`` run did to the study folder beyond what it wrote.

    Each run replaces the outcome of the one before. Without these lines a run
    on the wrong folder looks like a success, and the unblinded files of an
    earlier check vanish without a word.
    """
    if result.n_files_read == 0:
        print(
            "Note: no returned file was read, so nothing was checked: no .xlsx file was found. "
            "You may have given the wrong folder."
        )
    if result.removed:
        print(f"Removed, because they belonged to an earlier check: {_listed(result.removed)}.")
    if RATINGS_NAME in result.removed:
        when = "" if result.ok else " once a check has passed"
        print(f"  To get the unblinded files back, run 'ratersheet unblind' again{when}.")
    if (study_dir / METHODS_NAME).exists():
        print(
            f"Note: {METHODS_NAME} was written before this check and describes the state before "
            "it; run 'ratersheet methods' again."
        )


def _run_verify(args: argparse.Namespace) -> int:
    mismatches = 0
    try:
        key_sha256 = verify_key(args.study_dir)
        print(f"key: OK (matches {MANIFEST_NAME}; SHA-256 {key_sha256})")
    except IntegrityError as exc:
        mismatches += 1
        print(f"key: MISMATCH ({exc})")
    not_read = 0
    for sheet in args.sheets:
        # One unreadable file must not hide the verdict on the sheets after it.
        try:
            matches = verify_sheet(args.study_dir, sheet)
        except RatersheetError as exc:
            not_read += 1
            print(f"{sheet}: NOT READ ({exc})")
            continue
        if matches:
            print(f"{sheet}: OK (carries this study's key commitment)")
        else:
            mismatches += 1
            print(f"{sheet}: MISMATCH (carries the key commitment of another study or build)")
    if not args.sheets:
        example = Path(args.study_dir) / SHEETS_DIR / "*.xlsx"
        print(f"No sheets were given; to check some, add for example {example}")
    if not_read:
        print(f"error: {_plural(not_read, 'sheet')} could not be read", file=sys.stderr)
        return 2
    return 1 if mismatches else 0


def _run_unblind(args: argparse.Namespace) -> int:
    result = unblind(args.study_dir, allow_incomplete=args.allow_incomplete)
    print(f"wrote {result.ratings_path}")
    if result.comments_path is not None:
        print(f"wrote {result.comments_path}")
    print(f"wrote {result.record_path}")
    written = [result.ratings_path, result.comments_path]
    names = " and ".join(path.name for path in written if path is not None)
    print(
        f"Caution: the unblinded output ({names}) is not in {PRIVATE_DIR}/. It names the source "
        "behind every rating id, including rows not yet returned; keep it from anyone who is "
        "still rating."
    )
    if not result.complete:
        print("Note: collection was incomplete when the key was opened; the record says so.")
    return 0


def _run_methods(args: argparse.Namespace) -> int:
    path = write_methods(args.study_dir)
    print(path)
    print(
        f"Caution: {path.name} is not in {PRIVATE_DIR}/. It names the sources, and the kinds of "
        "leak warning if the checks raised any; keep it from anyone who is still rating."
    )
    return 0
