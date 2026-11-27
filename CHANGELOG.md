# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-27

First release.

### Added

- Study definition from a TOML study file and a CSV items file, with strict validation:
  unknown keys, badly quoted CSV fields, texts beyond the 32,767-character cell limit and
  sources that differ only in letter case are errors.
- Balanced assignment of items to raters: the number of items per rater differs by at most
  one, overall and within each source, and co-rating is spread over pairs of raters as evenly
  as those quotas allow. Each rater gets an independent random order; items can be grouped by
  case.
- Randomness from a generator specified in the documentation and pinned by tests, keyed by
  the study seed and a 256-bit secret drawn at build time, so that a design can be
  regenerated from its key on any Python version and cannot be predicted from the seed.
- One Excel workbook per rater, with items identified only by random codes, texts stored as
  text (never as formulas), drop-down rating cells, optional comments and protected
  non-rating cells.
- An unblinding key kept in a private folder, with a SHA-256 commitment to it embedded in
  every sheet and recorded in a manifest that contains nothing derived from the sources.
- Leak checks for source names, self-identifying phrases, text length and markup that could
  reveal the source, with a report of the comparisons that could not be made.
- Validation of returned workbooks (altered texts, invalid values, missing or duplicated
  rows, wrong study, wrong rater, altered sheet description), with a report and blinded CSV
  outputs that are written only when no error was found.
- Unblinding into `ratings.csv` and `comments.csv`, with key verification and a record of
  when the key was first used and how complete collection was at that moment.
- A methods text that states only what the tool did or measured, with explicit
  `[AUTHOR: ...]` placeholders for everything else.
- A command-line interface: `init`, `check`, `build`, `collect`, `verify`, `unblind`
  and `methods`.

[0.1.0]: https://github.com/sumesh-ramasamy/ratersheet/releases/tag/v0.1.0
