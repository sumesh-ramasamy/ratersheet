# Demo study

A small worked example for ratersheet.

- `study.toml` defines the study: three raters, two raters per item, an accuracy scale (1 to 5),
  a harm scale (none, minor, serious), comments and a seed.
- `items.csv` holds twelve items: four patient questions, each answered by three sources
  (`model_a`, `model_b` and `clinician`). The question is in the `context` column and the answer
  is in the `text` column.

All content is synthetic. The questions and answers were written for this example, contain only
general health information and are not medical advice. The source labels are arbitrary.

Try it from the repository root:

```
uv run ratersheet check examples/demo/study.toml
uv run ratersheet build examples/demo/study.toml --out demo-out
```

`check` reports how many items each rater will get. Who rates what, the order and the codes
are drawn when you build, from a random secret that is stored in the key, so two builds of
this study give different sheets. `demo-out/` is listed in `.gitignore`; do not commit a
built study to a repository that raters can read.
