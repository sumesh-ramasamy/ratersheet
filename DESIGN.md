# Design decisions

This document explains why ratersheet works the way it does. Each section states a decision,
the reason for it, what it costs, and what was rejected. It is written so that every choice
can be questioned and defended.

## 1. Scope: the design side of a rating study, and nothing else

**Decision.** ratersheet covers what happens before and around the ratings: who rates what,
in which order, under which codes, what comes back, and what can honestly be written about it.
It does not compute agreement statistics.

**Why.** Libraries for kappa, Krippendorff's alpha, Gwet's AC and the ICC already exist and
are maintained (`krippendorff`, `irrCAC`, `statsmodels`, `pingouin`). Adding another
implementation would add risk and no capability. What nobody packages is the step before:
studies rebuild the blinded sheets by hand each time, and the hand-made versions share the
same defects (see the README).

**Cost.** A user needs a second tool for the analysis. `ratings.csv` is a plain long-format
table so that any of those libraries can read it.

## 2. Spreadsheets, not a web application

**Decision.** Each rater gets one XLSX file. There is no server, account or browser form.

**Why.** Raters in these studies are often clinicians. A spreadsheet opens on any computer,
works offline, and can be sent through whatever channel the institution already approves.
Only one person on the team, the one who runs the tool, needs Python.

**Why XLSX and not CSV.** XLSX lets the tool mark each text as text (so a text beginning with
`=` cannot become a formula), offer the allowed values as a drop-down list, and lock the
cells a rater should not change. CSV can do none of these and its encoding and separator are
guessed differently by different programs.

**Cost.** Server-based tools can do things a file cannot, such as timing each rating or
preventing a rater from going back. Teams that need those should use one.

## 3. A study file in TOML, where a typo is an error

**Decision.** The study is described in one TOML file plus one CSV of items. Unknown keys and
tables are rejected.

**Why TOML.** Python reads it from the standard library, so there is no extra dependency, and
it has none of YAML's silent type conversions (`no` becoming `false`, `1.10` becoming `1.1`).

**Why reject unknown keys.** A misspelled option that is silently ignored changes the study
without anyone noticing: `rater_per_item = 2` would quietly give a fully crossed design.

**The items file is read strictly.** A badly quoted field is an error, not something to be
repaired by guessing: a lenient reader drops stray quote marks and can swallow the following
rows into one text, and the rater would then see a text that is not in the file. Source and
case labels are compared after Unicode normalisation, and two sources whose labels differ
only in letter case are refused, because that is nearly always one source typed two ways.

**Extra columns in the items file are ignored, and listed.** Researchers keep notes in their
item tables. Only `context` and `text` are ever shown to a rater, so an extra column cannot
leak. `ratersheet check` lists what was ignored.

## 4. Randomness: reproducible from the key, unpredictable from the seed

**Decision.** Every random choice comes from a small generator built on SHA-256. Python's
`random` module is not used. The generator is keyed by two things: the seed in the study file,
and a 256-bit random secret drawn when the sheets are built and stored only in the key.

**Why our own generator.** A study's design should be reproducible years later by a reviewer
on another machine. Python guarantees the stream of `random.random()` for a given seed, but
the algorithms behind `shuffle` and `sample` have changed between versions. The generator
here is specified in a few lines (hash a counter, read 8 bytes, reject values that would bias
the result, take the remainder), and a test pins its output with an independent
reimplementation and fixed expected values.

**Why a secret as well as a seed.** The first draft keyed everything on the seed alone, and
review showed that this breaks blinding. Seeds that people choose are small numbers, the tool
is open source, and a single rating id is enough to test a guess. A reviewer recovered the
seed from one rater's sheet in about a second, replayed that rater's shuffle, and so learned
each row's position in the items file. In a typical items file (cases in order, sources in a
fixed order within each case) the position gives away which texts share a source. With the
secret mixed into every stream, nothing a rater or a blind analyst holds follows from the
seed. A test replays the attack.

**Cost.** A study is no longer reproducible from the study file alone. It is reproducible
from the key, which holds both the seed and the secret and can be published after
unblinding. `ratersheet check` therefore cannot preview who will rate what; it reports what
is certain before the build (how many items each rater gets, overall and per source), and
`ratersheet build` prints the design that was actually drawn. Building the same study twice
gives two different designs.

**Why keep the seed at all.** It is the part of the randomisation the researcher chooses and
can state in a protocol. Stating it reveals nothing, because the design also depends on the
secret.

**Three separate streams.** Who rates what, the order per rater, and the codes each draw from
their own stream, so that a presentation option such as `group_by_case` does not change who
rates what when the same key is replayed.

## 5. Assignment: balanced loads, guaranteed; balanced pairs, best effort

**Decision.** Items are handled one source at a time, in random order. For each source, every
rater first gets a quota: the source's rating slots (items times raters per item) are divided
equally, and any remainder goes, one extra item each, to the raters who so far have the
fewest items overall (among raters with equal totals, to those with the fewest items in
common with the raters already given an extra, which is what spreads co-rating when sources
have a single item each). Each item is then given to raters who still have quota for that source.
A rater whose remaining quota equals the number of items left must take every one of them.
The other seats go, one at a time, to the rater with the fewest items in common with the
raters already chosen for this item, then the most quota left, with ties broken at random.

**What is guaranteed.** Every item gets exactly the requested number of distinct raters. For
every source, the number of items of that source per rater differs by at most one. The total
number of items per rater differs by at most one. Tests check these over several thousand
study shapes.

**Why they hold.** Per source, quotas are either the base number or one more, and every
quota is used up exactly: that is the per-source guarantee. Before each source the totals
differ by at most one, and the extra items go to the raters with the lowest totals, so the
totals still differ by at most one afterwards. The assignment can never get stuck: the
remaining quotas always add up to (raters per item) times (items left) and none exceeds the
items left, so there are never more raters who must be chosen than seats, and always enough
raters with quota to fill them.

**What is best effort.** Because the quotas alone secure the loads, the choice among eligible
raters is free to spread co-rating over pairs of raters, so that agreement can be estimated
for every pair. This is a greedy rule, not a balanced incomplete block design. In a study
with 4 raters, 2 raters per item and two sources of 25 items, the pair counts came out within
one of each other in 200 of 200 runs. When there are few items and many raters, some pairs
still share nothing (7 raters, 3 per item, 7 items is an example). `ratersheet build` prints
the pair counts and says so when a pair shares no item.

**What the per-source guarantee costs.** When not every rater rates every item, the rule
"each rater gets an equal share of every source" is public, and the pattern of who rated what
is visible to anyone who sees several sheets or the blinded ratings. Together they rule out
most ways of grouping the items into sources. A second review measured this: with 4 sources
of 2 items, 4 raters and 2 raters per item, the 105 possible groupings shrank to a median of
2. In the shape of the bundled example (3 sources of 4 items, 3 raters, 2 per item) 5,775
groupings shrank only to 1,728, and the effect fades as studies grow. It reveals which items
belong together, not which group is which source, and a single rater with only their own
sheet learns very little. No assignment rule can both balance sources across raters and hide
this. A fully crossed design has no such pattern, and `build` prints a note when the design
is not fully crossed. Report it as a limitation of a small partial design.

**How this changed in review.** The first draft chose raters by one combined rule: fewest
items of this source, then fewest overall, then fewest in common. The loads were right, but
the first two parts almost always decided the choice, so the pair rule never applied: the
same study gave pair counts of 4, 4, 4, 4, 17 and 17. Separating the load guarantee (quotas)
from the pair rule fixed it.

**Rejected alternatives.** Pure random assignment gives unequal workloads and, in small
studies, raters who see almost nothing from one source. A fixed rotation gives equal loads
but always pairs the same raters. Textbook block designs exist only for particular
combinations of items, raters and raters per item; real studies rarely have those numbers.

## 6. Codes: one per rater and item, carrying no information

**Decision.** Each row of a sheet has a rating id of the form `XXXX-XXXX`, unique to that rater
and that item. A separate item code (`I-XXXXXX`) identifies the item across raters in the
blinded dataset.

**Why not one letter per source.** A common hand-made scheme labels every answer from one
model "A". A rater who recognises a single answer then knows all of that model's answers.
Here no code is shared between items.

**Why a different code per rater.** Two raters comparing sheets cannot match items by code,
and a row pasted into the wrong rater's file is detected.

**Why the alphabet has no I, L, O, 0 or 1.** Codes are sometimes read aloud or retyped when a
rater reports a problem.

## 7. The key commitment: what it proves and what it does not

**Decision.** The unblinding key is a JSON file kept by the study lead. Its SHA-256 hash is
written into every rating sheet when the sheets are generated, and into the manifest.

**What it proves.** The raters hold the hash before any rating exists. If the key were
altered afterwards (to move a favourable rating from one source to another, say), its hash
would no longer match the value in the sheets already sent out. Anyone can check this without
ratersheet: compute the SHA-256 of `key.json` and compare it with the line on the
Instructions sheet.

**Why there is a nonce.** The key contains a random 256-bit value. Without it, someone who
knew the items and could guess the labels could hash their guesses and confirm one. With it,
the hash reveals nothing about the key.

**What the tool's own check compares.** `ratersheet verify` compares the key with the
manifest, and with the commitment inside any sheet it is given. The manifest sits beside the
key and could be edited with it, so the comparison that matters is with a sheet that was
sent out. The methods text says which comparison was made.

**What it does not prove.** It does not show that nobody read the key. Blinding still depends
on the study lead keeping `private/` away from the raters; the methods text therefore asks
the author to state who held the key. It also does not protect a study in which no sheet was
ever sent: someone who can edit both the key and the manifest is only caught by a sheet in
someone else's hands.

**Rejected alternative.** Digital signatures or a public timestamping service would be
stronger, and would need accounts, keys or network access. A hash that anyone can recompute
fits a tool that runs offline on one laptop.

## 8. Text integrity by hash, compared after light normalisation

**Decision.** The key stores a SHA-256 hash of every text and context. When a sheet comes
back, each text cell is hashed again; a mismatch is an error (`text_changed`).

**Why.** A rating is only meaningful for the text that was shown. A cell edited by accident
(or autocorrected, or truncated) silently changes what was rated.

**Normalisation.** Before hashing, text is converted to Unicode NFC, Windows and old Mac line
endings become `\n`, and leading and trailing white space is removed. Spreadsheet programs
change exactly these things on save without changing what a rater reads. Anything else is a
real difference.

**The one change the tool makes to a text.** Windows line endings (CR LF) are written to the
sheets as plain line breaks, because that is what a spreadsheet cell uses and the file format
cannot carry a bare carriage return. The hash comparison ignores that difference. Nothing
else in a text is altered.

## 9. Returned workbooks are untrusted input

**Decision.** Nothing in a returned workbook is evaluated. Formulas are read as text and
reported as invalid. XML is parsed with `defusedxml`. A sheet that extends beyond 100,000
rows is refused.

**Why.** Returned files come from other people's machines. A formula in a rating cell is at
best a mistake. `defusedxml` blocks the XML tricks that make a small file expand to gigabytes
in memory; openpyxl uses it automatically when it is installed, so it is a declared
dependency and ratersheet refuses to read workbooks without it.

**Comments in CSV.** A rater's free-text comment that begins with `=`, `+`, `-` or `@` is
written to the CSV with a leading apostrophe. Without it, a spreadsheet program opening the
CSV would try to run the comment as a formula. The cost is that such a comment is stored with
one extra character. Ratings are never affected: they are checked against the scale.

**The sheet's own description is not trusted either.** Each workbook carries a hidden sheet
that says which column is which. `collect` compares it with what `build` wrote for this
study; a returned file whose description differs is rejected, so nobody can swap two scales
or point the text check at another column.

**Escaped characters.** The XLSX format treats a sequence such as `_x0041_` inside a string
as an escaped character. Texts that contain such a sequence literally are escaped when
written and decoded when read, so that the rater sees the text as it is and the hash still
matches after a spreadsheet program has saved the file.

**Known gap.** There is no limit on the size of a returned file before it is opened. A
deliberately huge workbook could still exhaust memory.

## 10. Sheet protection is a guard rail, not security

**Decision.** The Ratings sheet is protected without a password. Rating and comment cells are
unlocked; texts and codes are locked.

**Why.** It prevents the common accident of typing over a text. Anyone can remove the
protection in two clicks, and the documentation says so. What actually detects changes is the
hash comparison in section 8.

## 11. Leak checks report; they never rewrite

**Decision.** Before building, the tool looks for four kinds of cue to the source in what a
rater will read: a source label in a text, a phrase by which a model names itself or its
vendor, a marked difference in length between two sources, and a marked difference in the use
of markup between two sources. It prints warnings. It never changes a text.

**What is compared.** Names and phrases are searched in the text and in the context, after
folding typographic variants (curly apostrophes, the several kinds of hyphen and dash,
invisible characters), because language models often write "GPT‑4" with a non-breaking
hyphen. Length and markup are compared for the text and, separately, for the context.

**Saying what was not checked.** Length and markup are only compared between two sources that
have at least 5 texts each, and source labels shorter than 3 characters are not searched for.
When a comparison cannot run, `check` and `build` print it under "Not checked", and the
methods text states how many labels were searched and how many pairs were compared. "No
warnings" must never be read as "checked and clean" when nothing was compared.

**Why report only.** The texts are the study's stimuli. Removing "As an AI language model"
or stripping markdown changes what is being rated, and whether to do that is a
methodological decision that belongs to the researcher and must be reported.

**The thresholds are judgement calls.** The length rule fires when, taking one random text
from each of two sources, the longer one comes from the same source at least 9 times in 10.
The markup rule fires when at least 80% of one source's texts use markup and at most 20% of
the other's do. Both need at least 5 texts per source. These numbers are meant to catch cues a
rater would notice without drowning small studies in warnings. They are not derived from a
published standard, and they are module constants so that they can be argued with.

**Limits.** These are heuristics. They cannot detect a model's writing style, and no warning
does not mean no cue. The methods text says this in the same words.

## 12. Collection fails closed

**Decision.** `collect` writes a dataset only if there is no error in any returned sheet.
With errors, it writes the report and nothing else.

**Why.** The alternative, writing the good rows and skipping the bad ones, produces a dataset
that looks complete and is not. A researcher should have to resolve a damaged sheet (usually
by asking the rater) before any analysis file exists.

**Lenient about representation, strict about meaning.** A 4 arrives as `4`, `4.0` or `"4"`
depending on the program and the cell format; all are accepted. `4.5`, `TRUE`, a date or a
formula are not. Nominal values are matched ignoring case and stored in the scale's own
spelling.

**Rows are matched by code, not position.** A rater who sorts the sheet does no harm.

**Missing is not wrong.** An empty required rating, or a rater who has not returned a file
yet, is a warning, so that a study can be collected in stages. `--require-complete` turns
these into errors for the final run. Collection counts as complete only when every required
rating is in and every rater with assignments has returned a sheet; counting ratings alone
would call a study with no required scale complete before any sheet came back.

**Nominal values are matched as a person would read them.** Case, the style of an
apostrophe, the form of an accented letter and spacing are ignored, and the value is stored
in the scale's own spelling. Nominal rating cells are formatted as text so that a value such
as `TRUE` or `0.5` is not turned into a boolean or a number by the spreadsheet program.

**A failed run leaves nothing that looks valid.** The old report is removed first and the new
one is written last, so an interrupted run cannot leave a passing report beside an older
dataset. When a folder is scanned, files whose names start with a dot or with `~$` are
skipped; they are lock files and the hidden companion files some systems write.

**Two files from one rater: neither is used.** The tool cannot know which one the rater
meant, and guessing would be a silent choice about data.

## 13. Records that cannot be quietly rewritten

**Decision.** `unblind` refuses to run unless the last `collect` passed, and refuses an
incomplete collection unless told otherwise. It records when the key was first used and how
complete collection was at that moment. That first record is kept through every later run.
`collect` never deletes it. Every `collect` run removes `ratings.csv`, so the unblinded file
can never be older than the report beside it.

**Why.** In a blinded study the important fact is when the key was first opened. If a later
run could overwrite the record, opening the key early could be made to disappear by simply
unblinding again after collection was complete. The methods text reports the first
unblinding and how many times the step was run.

The record is written before any unblinded file, so a failure part-way cannot leave sources
on disk with no record that the key was used. Each collection run has its own random id, and
the unblinding record notes which run it followed; that, not the clock, is how the methods
text knows whether the sheets were checked again after the key was first used. The
collection report also records the hash of the blinded files it wrote, and `unblind` refuses
a blinded file that is no longer the one the last collection wrote. If ratings are collected again after the first
unblinding, the methods text says so and asks the author to state whether any rating changed.

**Limit.** This records what the tool did. It cannot know whether someone opened `key.json`
in a text editor.

## 14. The blinded dataset contains no group labels

**Decision.** `ratings_blinded.csv` has item codes, rater ids, scales and values. It has no
source, not even a masked one such as "group A".

**Why.** Agreement between raters can be computed without knowing sources. Masked group
labels would let whoever holds the file see which items belong together, and in small teams
the analyst is often also a rater.

**Cost.** Analyses that compare sources need the unblinded file. That is intended: comparing
sources is the moment of unblinding.

**Limit.** The file does show who rated which item and in which position. In a design that is
not fully crossed that pattern narrows which items share a source (section 5), and with
`group_by_case` the positions show which items belong to one case. An analyst who must stay
fully blind in a small partial design should be given the ratings without the rater ids, or
the design should be fully crossed.

## 15. The methods text says only what the tool knows

**Decision.** `ratersheet methods` drafts a methods section from the records in the study
folder. Every statement in it is something the tool did or measured. Everything else is an
explicit placeholder beginning `[AUTHOR: ...]`: who the raters were, how the sample size was
chosen, who held the key, whether raters worked independently, what was done about each leak
warning.

**Why.** A generated paragraph that says "raters were blinded and worked independently" would
be pasted into papers whether or not it was true. The tool can state that the sheets carried
no source field; it cannot state that nobody told the raters.

**Reporting guidelines.** The text ends with a table that maps its statements to items of the
CHART and GRRAS reporting guidelines. The items are paraphrased, the author is told to check
them against the published checklists, and the table says that it is not a claim that either
checklist has been satisfied.

## 16. The manifest is safe to share; the key is not

**Decision.** `manifest.json` describes the study without source labels, item ids, case ids or
the seed, and without anything derived from the sources. What `build` writes that could
unblind lives under `private/`, which is created readable only by its owner on macOS and
Linux.

**Why.** Files get attached to emails and committed to repositories. One folder to keep away
from raters is a rule people can follow. A test searches the manifest for every source label
and item id. An earlier draft listed the kinds of leak warning in the manifest; review
pointed out that "the sources differ in length" is itself a hint, so they moved to the
private leak report.

**Files outside `private/` that name sources.** `ratings.csv` and `comments.csv`, written by
`unblind`, and `methods.md`, whenever it is written (it can be drafted straight after
`build`), name sources and sit at the top of the study folder, because that is where an
analyst looks for them. `unblind` and `methods` print a caution when they write them. They
must be kept from anyone still rating.

## 17. Few dependencies

**Decision.** Two runtime dependencies (`openpyxl`, `defusedxml`), the standard library for
everything else (`argparse`, `tomllib`, `csv`, `hashlib`), Python 3.11 or later.

**Why.** A research tool should still install in five years. Every dependency is a way for
that to fail. Python 3.11 is the first version with a TOML reader in the standard library.

## 18. Refusing to rebuild over a study in progress

**Decision.** `build` stops if the output folder already holds any file it would replace or
delete, unless `--overwrite` is given. A plain build never deletes anything.

**Why.** A rebuild creates a new key. Sheets already sent to raters would then fail
collection as belonging to another study. This is the most damaging mistake the tool makes
possible, so it requires an explicit flag. The check looks at every output file, not only
the manifest: an earlier draft checked the manifest alone, and a folder that had lost its
manifest would have had its key silently replaced.

## Known limitations

- The generated workbooks are tested by reading them back with openpyxl, including a copy
  stored the way Excel stores strings. They have not been tested automatically in Excel,
  LibreOffice, Numbers or Google Sheets. Open one sheet in the program your raters use before
  sending them out.
- A rater can often recognise a model from its style. No tool can prevent that; report it as
  a limitation of the study.
- Only single-item rating is supported. Side-by-side comparison, ranking and repeated items
  for intra-rater reliability are not.
- `build`, `collect` and `unblind` are the names of both a function and a module in the
  package. `from ratersheet import collect` gives the function, and
  `from ratersheet.collect import Problem` works, but `import ratersheet.collect as m` gives
  the function, not the module.
- On a shared computer, another user of the same machine cannot read `private/`. A team that
  shares a study folder through a group must widen those permissions themselves.
- Titles, source labels and scale labels are written into the methods text as given. Keep
  them short and free of markup.

## How this was built, and what review changed

The source code, tests and documentation were drafted with an AI coding assistant. The author
chose the problem, set the requirements and constraints, and directed and reviewed the
design.

The library and its tests were written separately from one written contract: the tests were
written without sight of the implementation, so that they check the contract and not the
code. The result was then reviewed from five angles (blinding, returned sheets, assignment
and randomness, robustness, and the truth of every generated sentence). Each reported
problem had to be reproduced independently before it was accepted, and each fix has a test
that fails without it.

That review found three serious faults in the first draft, all described above: codes and
order that could be predicted from a guessable seed (section 4), co-rating that was badly
unbalanced (section 5), and a rebuild that could replace a key without being asked to
(section 18). It also found about twenty-five smaller ones, most of them sentences in the
methods text that were false in some state of a study, for example "the items were rated by
3 raters" before any sheet had come back. A second review of the fixed code confirmed those fixes and found smaller problems, one of
them introduced by a fix (a text near the cell-size limit could be cut off); those were fixed
too. It also measured the limit described in section 5, which is a property of the design
and is documented, not removed.
