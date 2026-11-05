"""The tool registry behind `fbm tool`: signatures, argument coercion/validation and error-safe calls."""

import pytest

from fbm import tools
from fbm.tools import TOOLS, bind, coerce


def test_every_tool_is_registered_with_docs():
    assert len(TOOLS) > 50
    for t in TOOLS.values():
        assert t.doc, f"{t.name} needs a docstring (agents read it via `fbm tool {t.name} --doc`)"
    assert TOOLS["create_proposal"].write and not TOOLS["context_pack"].write


def test_coerce_by_type_hint():
    assert coerce("123", str) == "123"
    assert coerce('{"add": 1}', str) == '{"add": 1}'
    assert coerce("null", str | None) is None
    assert coerce("2026-10-21", str | None) == "2026-10-21"
    assert coerce("25", int) == 25 and coerce(25, int) == 25
    assert coerce("0.5", float | None) == 0.5 and coerce(1, float) == 1.0
    assert coerce("true", bool) is True and coerce(False, bool) is False
    assert coerce({"a": 1}, str) == '{"a": 1}'
    for bad, hint in (("x", int), ("maybe", bool), ("null", int), (True, int), ("1.5", int)):
        with pytest.raises(ValueError):
            coerce(bad, hint)


def test_bind_validates_names_and_required():
    assert bind("free_agents", {"top": "5", "sort": "week"}) == {"top": 5, "sort": "week"}
    with pytest.raises(ValueError, match="unknown argument"):
        bind("free_agents", {"nope": "1"})
    with pytest.raises(ValueError, match="missing required"):
        bind("player_card", {})


def test_tool_errors_come_back_as_data(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(tools.svc, "constants_table", boom)
    assert tools.call("constants") == {"error": "RuntimeError: down"}


def test_heavy_tools_have_tight_default_limits():
    defaults = {n: {p: v.default for p, v in TOOLS[n].params.items()} for n in TOOLS}
    assert defaults["signals"]["top"] == 15 and defaults["buy_sell"]["top"] == 8
    assert defaults["decisions_log"]["limit"] == 15 and defaults["grade_decisions"]["limit"] == 10
    assert defaults["find_trades"]["limit"] == 4 and defaults["ownership_trends"]["top"] == 10
    assert defaults["context_pack"] == {"notes_chars": 300, "signals_top": 8}
