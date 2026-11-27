# Contributing to ratersheet

Thanks for helping. This is a small tool, and small, focused changes are the easiest to review.

## Set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer.

```
git clone https://github.com/sumesh-ramasamy/ratersheet
cd ratersheet
uv sync
uv run pytest
```

## Ground rules

These keep the tool trustworthy for research use. Changes that break them will not be merged.

- No network access. The tool never sends data anywhere.
- Never modify a study's texts. Leak checks report problems; they do not rewrite anything.
- Every random choice in a design comes from the project's own generator (`DeterministicRNG`),
  keyed by the study seed and the secret stored in the key, never from the `random` module,
  so a design can be regenerated exactly from its key on any Python version. The secret
  itself is drawn with `secrets`.
- Nothing a rater receives, and nothing described as safe to share, may depend on the seed
  alone or carry anything derived from the sources.
- The methods text asserts only what the tool itself did or measured. Anything the tool
  cannot know stays an explicit `[AUTHOR: ...]` placeholder.

Statistical or methodological changes (for example to the assignment algorithm or the leak
checks) need a reference: a paper, a standard or a documented method that the change follows.

## Issues

A good issue says what you did, what you expected and what happened. Include the version
(`ratersheet --version`), your Python version and operating system, and a small synthetic
study file that reproduces the problem. Please do not attach real rater data or real patient
or participant content.

## Pull requests

- Keep one change per pull request, and explain why it is needed.
- Add or update tests, and make sure `uv run pytest` passes.
- Update `CHANGELOG.md` for anything a user would notice.
- Use only synthetic example content.
