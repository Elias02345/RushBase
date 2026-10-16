# Evaluation

The golden set holds expected values for core fields (shot size, camera angle, interior/exterior,
time of day, people count, person identifiable) and single-valued taxonomy fields, per shot (video)
or per asset (photo). Label it in the web UI (`/golden`: number keys choose, Enter accepts the AI
guess, arrows move, N skips) or with `PUT /api/v1/golden/labels`.

Human corrections of an AI value are copied into the golden set (`origin = correction`) unless the
app setting `golden.from_corrections` is `false`.

## Running an evaluation

```sh
uv run rushbase-server eval                       # prints the overall accuracy, stores an eval_run
uv run rushbase-server eval --json                # full metrics as JSON
uv run rushbase-server eval --out docs/eval/      # also writes docs/eval/<date>.md
```

The same is available as `POST /api/v1/admin/eval` and in Administration, tab Search.

Predictions come from the **latest successful `vision` and `classify` stage results** (the AI's own
answer, not the human-corrected columns). Missing predictions count as `(none)`. Per field the report
lists n, accuracy, per-class precision/recall/F1, macro F1 and the confusion matrix.

`server/tests/test_evaluation.py` holds a small fixture regression (no models, no database) that
pins the metric maths.

## Model comparisons

Record A/B results (for example the gpu8 VLM choice from ADR-0011) as dated Markdown reports in this
directory.
