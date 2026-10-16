# Contributing

## Setup
```
uv sync
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

## Principles
- Engine logic (`src/fbm`) is pure, deterministic and covered by tests; the LLM layer only reasons over engine output.
- Numbers are never invented: every stat/projection must come from engine code.
- Tunable numbers belong in the constants registry (`fbm.constants`), not inline magic values.
- League rules come from Yahoo settings, never hard-coded.
- No new Yahoo write calls outside the engine's guarded functions; tests must never hit Yahoo.
- Tests must be hermetic: no reliance on `data/`, `memory/`, local config or credentials.

## PR checklist
- [ ] Tests added/updated; `uv run pytest -q` passes
- [ ] `ruff check` and `ruff format --check` pass
- [ ] `python scripts/prepublish_check.py` passes (no secrets/personal data)
- [ ] Docs updated if behaviour changed
