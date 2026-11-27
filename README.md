# ratersheet

[![CI](https://github.com/sumesh-ramasamy/ratersheet/actions/workflows/ci.yml/badge.svg)](https://github.com/sumesh-ramasamy/ratersheet/actions/workflows/ci.yml)

ratersheet builds blinded rating spreadsheets for human raters who score texts from several sources, for example language models and a human expert. It assigns the texts to raters in a balanced way, keeps a verifiable key to the sources, checks the sheets that come back, and drafts a methods paragraph.

## Why

Blinded rating sheets made by hand in a spreadsheet program tend to go wrong in the same ways:

- The key that links texts to sources is kept where raters can read it, or there is no proof that it was not changed after the sheets were sent.
- Every rater gets the items in the same order, so order effects cannot be separated from rater effects.
- Each model gets one code letter for all items. A rater who recognises one answer from model "C" then knows every answer from model "C".
- A text that begins with `=` is turned into a formula and shows an error or a number instead of the text.
- Returned sheets are pasted together with no checks. Edited texts, rows that were deleted and ratings outside the scale go unnoticed.
- Texts are cut off at the spreadsheet's limit of 32,767 characters per cell, and nobody notices.

ratersheet addresses each of these. It cannot address everything that can reveal a source; see "What it does not do".

## Install

ratersheet is not on PyPI yet. It needs Python 3.11 or later.

```
pip install git+https://github.com/sumesh-ramasamy/ratersheet
```

With uv:

```
uv tool install git+https://github.com/sumesh-ramasamy/ratersheet
```

This gives you the `ratersheet` command. `ratersheet init DIR` writes an example `study.toml` and `items.csv` into DIR. It refuses to run if either file already exists.

## A worked example

The repository contains a small study in `examples/demo`: twelve answers to four patient questions, written by three sources (`model_a`, `model_b` and `clinician`). All text is synthetic. Three raters each rate eight answers; each answer is rated by two raters. The commands below were run in a folder that holds a copy of `examples/demo` as `demo/`.

### Check

`check` shows what is certain before anything is built, and any leak warnings. It writes nothing.

```
$ ratersheet check demo/study.toml
Study: Demo: patient questions answered by three sources
12 items from 3 sources; 3 raters; 2 per item; 24 assignments
Raters: R1, R2, R3
Items per rater: 8
Items per rater from each source:
  clinician: 2 or 3
  model_a: 2 or 3
  model_b: 2 or 3
Who rates what, the order of each sheet and the codes are drawn at build time from a random secret kept in the key; they cannot be predicted from the study file.
Leak warnings: none
Not checked:
  Length and markup were not compared for 3 of 3 pairs of sources: a source with fewer than 5 items is not compared ('clinician', 'model_a', 'model_b').
```

`check` does not say which rater gets which item, because that is drawn at build time. It says what is certain: how many items each rater gets and how many from each source, give or take one. "Leak warnings: none" means that the checks that ran found nothing. The "Not checked" note lists comparisons that could not run: here every source has only four items, too few for the length and markup comparisons. It is information, not a warning, and it does not make `--strict` fail.

### Build

```
$ ratersheet build demo/study.toml --out demo-out
Built 3 rating sheets in demo-out
Items per rater (and per source):
  R1: 8 (clinician: 3, model_a: 3, model_b: 2)
  R2: 8 (clinician: 3, model_a: 2, model_b: 3)
  R3: 8 (clinician: 2, model_a: 3, model_b: 3)
Items rated by both raters of a pair:
  R1|R2: 4
  R1|R3: 4
  R2|R3: 4
Note: not every rater rates every item. Because each rater gets an equal share of every source, the pattern of who rated what narrows down which items share a source, strongly in a small study. Anyone who can see several sheets, or the blinded ratings, can use it. A fully crossed design (every rater rates every item) has no such pattern.
Send to the raters, one file each:
  R1: demo-out/sheets/R1.xlsx
  R2: demo-out/sheets/R2.xlsx
  R3: demo-out/sheets/R3.xlsx
Keep private; never send to raters:
  demo-out/private  (the unblinding key and the leak report)
Safe to share:
  demo-out/manifest.json  (no sources, item ids or seed)
Key commitment (SHA-256): d1eb6f3c8be8a7e9757eb524ad8798b98431a15b7b9d255107985b62a9a2ef47
Leak warnings: none
Not checked:
  Length and markup were not compared for 3 of 3 pairs of sources: a source with fewer than 5 items is not compared ('clinician', 'model_a', 'model_b').
```

The table is the design that was built. The study file does not fix it. `build` draws a random secret, stores it in the key, and draws who rates what, the order of each sheet and all codes from the seed and that secret together. Two builds of the same study therefore give different assignments, orders and codes. A second build of the demo, into another folder, gave R1 three clinician items, R2 two and R3 three, where the build above gave 3, 3 and 2. A study is reproducible from its key, not from the seed: the key records the seed and the secret, and the design can be regenerated from them. Keep the key.

Raters get the same number of items, give or take one, and the same number from each source, give or take one. Co-rating is spread over the pairs of raters as evenly as those quotas allow; this is best effort, not a guarantee. `build` prints the items each pair shares, and notes any pair that shares none, because agreement between them could not then be estimated. The key commitment also differs for every build, so yours will differ from the one above.

The note in the output is a limit of any design in which not every rater rates every item; see "What it does not do".

A plain `build` refuses to run if the output folder already holds any file it would replace or delete (a manifest, a key, a sheet in `sheets/`, a result of an earlier step), because a new key would orphan sheets that were already sent:

```
$ ratersheet build demo/study.toml --out demo-out
error: demo-out already holds a built study (manifest.json exists). Building again would create a new key, and sheets already sent to raters could no longer be collected. Choose another output folder, or pass overwrite (--overwrite) to discard the old study.
```

With `--overwrite`, `build` first deletes those files by name, and every `.xlsx` file in `sheets/`. The earlier key is lost.

On macOS and Linux the `private` folder is created readable only by its owner (`drwx------`), and `key.json` and `leak_report.json` in it are readable only by their owner (`-rw-------`), as `ls -l` shows.

### What the output folder contains

After `build` the folder holds `manifest.json`, `sheets/` and `private/`. Later steps add more files. Here is every file and who may see it:

| File | Who may see it |
|---|---|
| `sheets/R1.xlsx` (and one per rater) | That rater only |
| `manifest.json` | Anyone. It has no sources, item ids, seed or leak warnings. |
| `private/key.json` | The study lead only. It links every rating to its source. |
| `private/leak_report.json` | The study lead only. It names items. |
| `collect_report.json` | The study team. It lists problems in returned sheets. |
| `ratings_blinded.csv`, `comments_blinded.csv` | The study team, and an analyst who should stay blind. They have no sources. |
| `ratings.csv`, `comments.csv` | The study team, after unblinding. They name the source of every rating. Keep them from anyone who is still rating. |
| `unblind_record.json` | The study team. It records when the key was first opened. |
| `methods.md` | The study team. It names sources. Keep it from anyone who is still rating. |

An analyst can work with `ratings_blinded.csv` without seeing a source: the item codes and rating ids are drawn from the secret in the key and cannot be worked out from the seed or from the study file. The file does show who rated which item. In a design that is not fully crossed, that pattern narrows which items share a source (see "What it does not do"), and with `group_by_case` the positions show which items belong to one case. `study.toml` and `items.csv` name the sources, so they are not for that analyst either.

### What a rater sees

Each rater gets one `.xlsx` file with two visible sheets.

`Instructions` has the study title, the rater's id, your instructions, numbered steps for filling in the sheet, a table of the scales with their allowed values, descriptions and whether each is required, a line saying what "required" means, the key commitment, and the tool version.

`Ratings` has one row per item, in an order that is random and different for each rater. The columns are `rating_id` (a random code of the form `XXXX-XXXX`, different for every rater and item), `context` (here, the patient's question), `text` (the answer, in the column to the right of the question), one column per scale (`Accuracy`, `Potential harm`) and `comment`. Each rating cell has a drop-down list of the allowed values. The header row is frozen. The sheet is protected without a password so that only the rating and comment cells can be edited. Texts are stored as text, so one that starts with `=` stays text. The cells of nominal scales and the comment cells are formatted as text, so a value such as `TRUE` or `0.5`, or a comment such as `1/2`, stays as typed.

A hidden third sheet, `_meta`, records which columns are which. The source of an item appears nowhere in the file.

### Fill the sheets in

Real raters use a spreadsheet program. For this example, a short script plays the raters:

```python
import random
from pathlib import Path
import openpyxl

rng = random.Random(1)
Path("returned").mkdir(exist_ok=True)
for sheet in sorted(Path("demo-out/sheets").glob("*.xlsx")):
    wb = openpyxl.load_workbook(sheet)
    ws = wb["Ratings"]
    header = [c.value for c in ws[1]]
    accuracy, harm = header.index("Accuracy") + 1, header.index("Potential harm") + 1
    for row in range(2, ws.max_row + 1):
        ws.cell(row, accuracy).value = rng.randint(2, 5)
        ws.cell(row, harm).value = rng.choice(["none", "none", "minor"])
    wb.save(Path("returned") / sheet.name)
```

### Collect

`collect` reads the returned files (or a folder of them), checks them, and writes the blinded dataset only if there are no errors. When it scans a folder it skips files whose names start with a dot or with `~$`, such as the lock files a spreadsheet program leaves beside an open workbook. A file that you name yourself is always read.

```
$ ratersheet collect demo-out returned
Received 48 of 48 expected ratings (complete).
Report: demo-out/collect_report.json
OK: wrote demo-out/ratings_blinded.csv
OK: wrote demo-out/comments_blinded.csv
```

"Complete" means that every required rating was received and that every rater's sheet came back. With only two of the three sheets, `collect` reports `Received 32 of 48 expected ratings (incomplete).` and the missing sheet as a warning.

`ratings_blinded.csv` has one row per rating and no sources:

```
item_code,rater_id,scale,value,rating_id,position
I-AKDWPX,R1,accuracy,3,8W7H-3NHD,1
I-AKDWPX,R1,harm,minor,8W7H-3NHD,1
I-JTCYE6,R1,accuracy,2,YDEH-68EH,2
I-JTCYE6,R1,harm,none,YDEH-68EH,2
```

`item_code` is the same for every rater who rated that item, so you can compute agreement before anyone sees a source. In this example the raters wrote no comments, so `comments_blinded.csv` holds only its header line.

If something is wrong, no dataset is written. This is what happened when a rating of 7 was entered, one text was edited, and one rater's file was left out:

```
$ ratersheet collect bad-out badret
ERROR   invalid_value: R1.xlsx, row 2 (rating id NB77-9GMW): 7 is not an allowed value for 'Accuracy'; allowed: 1, 2, 3, 4, 5
ERROR   text_changed: R1.xlsx, row 3 (rating id YNGS-HVGS): the text is not the text that was sent to the rater
WARNING missing_workbook: no workbook was provided for rater R3
Received 31 of 48 expected ratings (incomplete).
Report: bad-out/collect_report.json
FAILED: 2 errors; no dataset was written.
```

The exit code is 1 when there are errors.

Every `collect` run first deletes the old `collect_report.json` and writes the new one last, so a run that stops half-way leaves no report. It deletes `ratings.csv` and `comments.csv` on every run, so that they are never older than the report; `unblind` writes them again. After a run with errors it deletes the blinded files too. It never deletes `unblind_record.json`, which is the evidence of when the key was first used. `collect` says which files it removed and reminds you to run `unblind` and `methods` again.

### Verify

`verify` checks that `private/key.json` still matches the commitment in `manifest.json`, and that each sheet you give it carries this study's commitment.

```
$ ratersheet verify demo-out returned/*.xlsx
key: OK (matches manifest.json; SHA-256 d1eb6f3c8be8a7e9757eb524ad8798b98431a15b7b9d255107985b62a9a2ef47)
returned/R1.xlsx: OK (carries this study's key commitment)
returned/R2.xlsx: OK (carries this study's key commitment)
returned/R3.xlsx: OK (carries this study's key commitment)
```

### Unblind

`unblind` refuses to run if the last `collect` found errors, if the key does not match the manifest, or if collection is incomplete, that is, if ratings are missing or any rater's sheet is outstanding (unless you pass `--allow-incomplete`). After `collect` on only two of the three sheets:

```
$ ratersheet unblind demo-out
error: collection is incomplete: 32 of 48 expected ratings were received and no sheet has been returned by R3. Collect what is missing first, or pass allow_incomplete (--allow-incomplete) to unblind anyway; the record will say it was incomplete.
```

After the full `collect` it writes the dataset with sources:

```
$ ratersheet unblind demo-out
wrote demo-out/ratings.csv
wrote demo-out/comments.csv
wrote demo-out/unblind_record.json
Caution: the unblinded output (ratings.csv and comments.csv) is not in private/. It names the source behind every rating id, including rows not yet returned; keep it from anyone who is still rating.
```

```
item_id,case_id,source,rater_id,scale,value,item_code,rating_id,position
q4_c,q4,clinician,R1,accuracy,3,I-AKDWPX,8W7H-3NHD,1
q4_c,q4,clinician,R1,harm,minor,I-AKDWPX,8W7H-3NHD,1
q2_b,q2,model_b,R1,accuracy,2,I-JTCYE6,YDEH-68EH,2
q2_b,q2,model_b,R1,harm,none,I-JTCYE6,YDEH-68EH,2
```

`unblind_record.json` records the time of the first use of the key (under `first`), the time of the latest run, how many times the key was used, and whether collection was complete. A later run does not overwrite the first time. `unblind` also refuses a `ratings_blinded.csv` that is no longer the file the last `collect` wrote.

### Methods

```
$ ratersheet methods demo-out
demo-out/methods.md
Caution: methods.md is not in private/. It names the sources, and the kinds of leak warning if the checks raised any; keep it from anyone who is still rating.
```

`methods.md` states only what the tool did or measured. Everything it cannot know is a placeholder that starts with `[AUTHOR: ...]`. You must replace each one. An excerpt:

> **Assignment and order.** Each item was assigned to 2 of the 3 raters (24 assignments in total; the design was not fully crossed). The assignment, the order of presentation and the codes were generated by the software from the study's random seed and a random value that is kept in the unblinding key. Each rater was assigned 8 items in total and between 2 and 3 items from any one source. The order of the items was randomised separately for each rater.
>
> **Blinding.** A separate spreadsheet was generated for each rater by ratersheet 0.1.0. The sheet showed the text of each item and, where one was supplied, its context, identified each row only by a random code, and had no field naming the source of an item. [...] [AUTHOR: state who held the unblinding key and confirm that it was not shared with the raters.] [AUTHOR: state whether the raters worked independently of one another.]
>
> **Check for identifying content (leak check).** [...] It did not compare the length or the markup of the texts or contexts for any of the 3 pairs of sources, because a source with fewer than 5 items is not compared. The checks that were made raised no warnings. [...]

The leak check paragraph says what was and was not compared.

The file ends with a table that links its statements to items of the CHART and GRRAS reporting guidelines. It says to check the wording against the published checklists and does not claim that either has been met.

## The study file

A study is a TOML file plus a CSV file of items. This is the demo's `study.toml`, with comments added:

```toml
[study]
title = "Demo: patient questions answered by three sources"   # required
seed = 20261126          # required, a whole number; kept in the key (see below)
instructions = "Rate each answer on its own merits. The question is in the context column, to the left of each answer."   # optional; shown on Instructions

[raters]
ids = ["R1", "R2", "R3"]   # required; become file names: letters, digits, _ and - only

[design]                   # the whole table is optional
raters_per_item = 2        # default: every rater rates every item
group_by_case = false      # true keeps items with the same case_id next to each other

[items]
file = "items.csv"         # required; relative to the folder of this file

[[scale]]                  # at least one scale
id = "accuracy"            # lowercase letters, digits and _
label = "Accuracy"         # column header; default is the id
type = "ordinal"           # whole numbers from min to max, at most 11 points
min = 1
max = 5
description = "1 = wrong, 5 = fully correct"   # optional

[[scale]]
id = "harm"
label = "Potential harm"
type = "nominal"           # a list of two or more words
values = ["none", "minor", "serious"]
# required = false: an empty cell in this column is not counted as missing (default: true)

[comments]                 # the whole table is optional
enabled = true             # adds a comment column; default false
```

The seed does not fix the design: the secret drawn at build time is part of it, so the same seed and files give a different design on every build. An unknown table or key is an error, so a typo is not silently ignored. The file may start with a byte order mark.

The items file is UTF-8 CSV. It is read strictly: a badly quoted field is an error that names the line, and it is not repaired. Quoted fields may span several lines.

| Column | Required | Meaning |
|---|---|---|
| `item_id` | yes | A unique id for the item. Never shown to raters. |
| `source` | yes | Who or what wrote the text, for example `model_a`. Never shown to raters. Two sources that differ only in letter case, such as `GPT-4` and `gpt-4`, are an error. |
| `text` | yes | The text to rate. At most 32,767 characters. |
| `case_id` | no | Groups items that answer the same question. Needed for `group_by_case`. Never shown to raters. |
| `context` | no | Text shown in the column to the left of the text, such as the question. At most 32,767 characters. |
| any other column | no | Ignored, and listed by `check`. Never shown to raters. |

A text longer than the cell limit is an error that names the item, for example `text is 32768 characters long; a spreadsheet cell holds at most 32767 characters`. ratersheet never edits your texts, with one exception: Windows line endings (CR LF) are written to the sheets as plain line breaks, because that is what a spreadsheet cell uses. The check of returned texts ignores that difference.

## What the checks do

### Leak checks

`check` and `build` look at the text and the context, which is all a rater sees, for four kinds of cue to the source:

- `source_name_in_text`: a source label of three or more characters, such as `model_a`, appears in a text or context.
- `self_identification`: a text or context contains a phrase by which a model names itself or its vendor, such as "as an AI" or "ChatGPT". A phrase that ends in a digit, such as "gpt-4", also matches longer names such as "GPT-4o".
- `length_separable`: for two sources with at least five items each, the length alone nearly identifies the source (a text from one is longer than a text from the other in at least 90% or at most 10% of comparisons). The texts are compared, and the contexts are compared separately.
- `formatting_separable`: for two sources with at least five items each, at least 80% of one source's texts use markup and at most 20% of the other's do. Markup here means a line that starts with a heading mark (`#` and a space), a list marker (`-`, `*`, `+`, a bullet character, or a number followed by `.` or `)`) or a quote mark (`>`), or a text that contains `**` or a backtick. The texts are compared, and the contexts are compared separately.

Before matching, names are folded: typographic apostrophes and dashes are made plain, invisible formatting characters are dropped, and accents and full-width letters are normalised. A name written with a curly apostrophe or a non-breaking hyphen is therefore still found. A spelling that is not on the phrase list, such as "Chat GPT" with a space, is not.

These are heuristics. They report and never rewrite your texts. No warning does not mean there is no cue. What to do about a warning is your decision. For example, with a phrase added to two of the demo's items:

```
$ ratersheet check demo-leak/study.toml
[...]
Leak warnings: 1
  [self_identification] 2 items contain a phrase by which a model names itself or its vendor ("as an ai", "ai language model", "language model"); a rater who reads it may guess the source.
      items: q1_a, q1_b
Not checked:
  Length and markup were not compared for 3 of 3 pairs of sources: a source with fewer than 5 items is not compared ('clinician', 'model_a', 'model_b').
```

Comparisons that could not run are reported under "Not checked", so that "none" is not mistaken for "checked and clean". There are two cases: a source label shorter than three characters is not searched for, and a source with fewer than five items is not compared with the others. `check` and `build` print the note, `private/leak_report.json` records it, and `methods.md` says what was and was not compared.

`check` exits 0 with warnings. Add `--strict` to exit 1 instead. `build --strict` writes nothing if there are warnings. The full warnings go to `private/leak_report.json`. `manifest.json` does not mention them, because which cue separates the sources would itself be a hint. `methods.md` states how many were raised and of which kind.

### Checks on returned sheets

`collect` matches each row to its assignment by `rating_id`, so a rater who sorts the rows does no harm. It checks the texts and contexts against hashes recorded in the key. It accepts `4`, `4.0` and `"4"` for an ordinal rating. For a nominal rating it accepts the value whatever its capitalisation, and tolerates a typographic apostrophe, a different form of an accent and different spacing, as long as exactly one value then fits. It rejects formulas, booleans and anything outside the scale.

Errors stop the dataset from being written.

| Code | Meaning |
|---|---|
| `not_a_ratersheet` | The file cannot be read as a ratersheet workbook, or its hidden sheet was altered. |
| `wrong_study` | The workbook carries the key commitment of another study or of an earlier build of this one. |
| `unknown_rater` | The workbook's rater id is not in the study. |
| `duplicate_workbook` | Two files came back for the same rater; neither is used. |
| `unknown_rating_id` | A row's rating id is not in the key. |
| `wrong_rater` | A row's rating id belongs to another rater. |
| `duplicate_rating_id` | The same rating id appears twice in one file. |
| `missing_row` | An assigned item is absent from the rater's file. |
| `text_changed` | The text cell no longer matches the text that was sent, or now holds a formula. |
| `context_changed` | The context cell no longer matches the context that was sent, or now holds a formula. |
| `invalid_value` | A rating is not an allowed value of its scale. |

Warnings do not stop the dataset from being written, unless you pass `--require-complete`, which turns them into errors.

| Code | Meaning |
|---|---|
| `missing_workbook` | No file came back for a rater. |
| `missing_value` | A required scale is empty for a row. |

## What the key commitment does and does not prove

At build time, ratersheet computes the SHA-256 hash of `private/key.json`, writes it to `manifest.json`, and puts it on the `Instructions` sheet of every rater's workbook. The key contains a random value, so the hash does not let anyone guess the key.

It shows that the key was not changed after the sheets were sent. Anyone who holds the key file can check this without the tool. Compute the SHA-256 of `key.json` and compare it with the value after "Key commitment (SHA-256)" on the Instructions sheet:

```
$ shasum -a 256 demo-out/private/key.json
d1eb6f3c8be8a7e9757eb524ad8798b98431a15b7b9d255107985b62a9a2ef47  demo-out/private/key.json
```

On Linux use `sha256sum`; on Windows, `certutil -hashfile key.json SHA256`.

The tool's own `verify` compares the key with the manifest and, when given sheets, with the commitment in those sheets. The check that matters is against a sheet that was sent out. The manifest sits in the same folder as the key, so a key and a manifest that were both replaced would still match each other, but not the commitment in a sheet that a rater already holds.

It does not show that nobody read the key. Only the study lead can say who had access, and the methods text leaves that for you to state. Sheet protection stops accidental edits. It is not security, and anyone can remove it.

## What it does not do

- It does not compute agreement statistics. Use a library on `ratings.csv`, for example [krippendorff](https://pypi.org/project/krippendorff/) or [irrCAC](https://pypi.org/project/irrCAC/).
- It has no web interface. Raters use a spreadsheet program, and you run the commands.
- It does not support pairwise or side-by-side designs yet. Each rater scores one text at a time.
- It cannot stop a rater from recognising a model's style. The leak checks only look for the cues listed above.
- It cannot hide the structure of a design in which not every rater rates every item. Each rater gets an equal share of every source, and who rated what is visible to anyone who sees several sheets or the blinded ratings. Together these narrow which items share a source. In a small study the narrowing can be strong: with 4 sources of 2 items, 4 raters and 2 raters per item, 105 possible groupings typically shrank to 2. In the shape of the demo it is weak (5,775 groupings shrank to 1,728), and it fades as studies grow. It shows which items belong together, not which group is which source. If this matters, let every rater rate every item, which is the default.
- The sheets have been tested by reading them back with openpyxl, not in Excel, LibreOffice, Numbers or Google Sheets. Open one sheet in the program your raters use before you send them out.

## Citing

See `CITATION.cff`. In plain text:

> Ramasamy, S. B. (2026). ratersheet (version 0.1.0) [Computer software]. https://github.com/sumesh-ramasamy/ratersheet

If you use ratersheet in a study, please open an issue so the study can be listed.

## AI assistance

The source code, tests and documentation of this project were drafted with an AI coding assistant (Claude, by Anthropic). The author chose the problem, set the requirements and constraints, and directed and reviewed the design. The design decisions and their trade-offs are recorded in DESIGN.md.

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. Copyright Sumesh Bharathi Ramasamy. See `LICENSE`.
