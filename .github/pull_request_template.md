## Summary


## Checklist
- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check src tests scripts` and `ruff format --check` pass
- [ ] `uv run python scripts/prepublish_check.py` passes
- [ ] No secrets or personal league data (memory/, reports/, data/, config/*.toml) included
- [ ] Engine changes are pure and tested; no numbers invented
