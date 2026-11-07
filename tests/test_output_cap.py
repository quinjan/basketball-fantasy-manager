"""Output budget for agent-facing tool results: small results pass through, big ones are shrunk and saved in full."""

import json

from fbm import output_cap
from fbm.output_cap import cap


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_small_results_and_errors_pass_through(tmp_path):
    small = {"a": [1, 2, 3]}
    assert cap("free_agents", small, 100, tmp_path) is small
    err = {"error": "x" * 500}
    assert cap("free_agents", err, 100, tmp_path) is err
    assert cap("free_agents", {"a": "x" * 500}, 0, tmp_path) == {"a": "x" * 500}
    assert not list(tmp_path.iterdir())


def test_action_critical_tools_are_never_cut(tmp_path):
    big = {"steps": ["step"] * 5000}
    for name in ("lineup_steps", "proposal_steps", "optimize_lineup", "get_proposal", "create_proposal"):
        assert cap(name, big, 1000, tmp_path) is big


def test_big_results_are_shrunk_saved_and_marked(tmp_path):
    big = {"rows": [{"n": i, "text": "y" * 200} for i in range(500)], "note": "z" * 5000}
    out = cap("rankings", big, 4000, tmp_path)
    assert len(_compact(out)) <= 4000
    meta = out["_truncated"]
    assert meta["original_chars"] > 4000
    saved = json.loads((tmp_path / "rankings.json").read_text(encoding="utf-8"))
    assert saved == big and meta["full_output"].endswith("rankings.json")
    assert out["rows"][-1].startswith("…[+") and out["note"].endswith("chars]")
    assert out["rows"][0] == {"n": 0, "text": "y" * 200}


def test_list_results_are_wrapped(tmp_path):
    out = cap("rankings", ["x" * 100] * 1000, 2000, tmp_path)
    assert len(_compact(out)) <= 2000 and "_truncated" in out and out["result"][0] == "x" * 100


def test_outline_when_nothing_fits(tmp_path):
    wide = {f"key{i}": i for i in range(2000)}
    out = cap("rankings", wide, 500, tmp_path)
    assert set(out) == {"outline", "_truncated"} and out["outline"]["key7"] == "<1 chars>"


def test_default_budget_is_about_three_k_tokens():
    assert output_cap.DEFAULT_MAX_CHARS == 12000
