# Contributing

onus is alpha; open an issue before a large change so we can agree on the shape.

```console
uv sync
uv run ruff check src tests evals
uv run mypy
uv run pytest -q --cov          # hermetic: no network, no API key
uv run python evals/run.py      # labelled eval, offline
```

- Tests must stay hermetic. Use pydantic-ai's `TestModel`/`FunctionModel`, never a live model.
- The deterministic tier must keep perfect recall and zero false positives on `evals/cases.json`.
  If you add a rule or change matching, add a labelled case.
- Fixtures must be fictional. Don't commit real documents, names or figures.
- Comments say why the code is correct, not what changed.
