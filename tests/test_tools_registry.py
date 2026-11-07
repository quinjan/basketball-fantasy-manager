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
    assert defaults["context_pack"] == {"notes_chars": 0, "signals_top": 5, "memory_chars": 1000, "journal": "titles"}


def _rows(n):
    return [
        {"key": f"{g}.k{i}", "value": i, "default": 0, "overridden": g == "stream" and i == 0, "source": "s" * 80}
        for g in ("stream", "health")
        for i in range(n)
    ]


def test_constants_is_compact_by_default(monkeypatch):
    table = {"constants": _rows(40), "unknown_overrides": [], "how_to_change": "edit"}
    monkeypatch.setattr(tools.svc, "constants_table", lambda: table)
    compact = tools.call("constants")
    assert compact["groups"] == {"stream": 40, "health": 40} and compact["total"] == 80
    assert [r["key"] for r in compact["overridden"]] == ["stream.k0"] and "constants" not in compact
    hit = tools.call("constants", {"key": "HEALTH.k39"})
    assert hit["matched"] == 1 and hit["constants"][0]["key"] == "health.k39"
    assert tools.call("constants", {"verbose": "true"}) == table


def test_hashtag_status_summarizes_imports_unless_detail(monkeypatch):
    from fbm.gm import hashtag_services

    imports = [{"page": "p", "view": str(i), "status": "ok", "finished_at": f"2026-11-0{i}"} for i in range(1, 5)]
    imports.append({"page": "waiver", "view": None, "status": "error", "finished_at": "2026-11-09", "detail": "x"})

    def fake():
        return {"health": {"healthy": True}, "last_imports": list(imports), "unmatched_players": list(range(30))}

    monkeypatch.setattr(hashtag_services, "hashtag_status", fake)
    out = tools.call("hashtag_status")
    assert "last_imports" not in out and out["health"] == {"healthy": True}
    s = out["last_imports_summary"]
    assert s["views"] == 5 and [r["page"] for r in s["not_ok"]] == ["waiver"]
    assert s["newest"] == "2026-11-09" and s["oldest"] == "2026-11-01"
    assert len(out["unmatched_players"]) == 10 and out["unmatched_players_total"] == 30
    assert len(tools.call("hashtag_status", {"detail": "true"})["last_imports"]) == 5


def test_refresh_data_is_compact_unless_verbose(monkeypatch):
    raw = {
        "jobs": ["a"],
        "hashtag_import": {
            "run_id": "r",
            "healthy": False,
            "health_reasons": ["x"],
            "weights": {"PTS": 1.0},
            "pages": {
                "p:ROS": {"status": "ok", "rows": 9, "detail": ""},
                "league_sync": {"status": "league_mismatch", "rows": 0, "detail": "who"},
            },
        },
        "rostered_injury_changes": [{"name": f"P{i}", "team": "X", "id": i, "comment": "c" * 300} for i in range(15)],
        "yahoo_snapshot": {"week": 3, "transactions": {"new": 0, "memory": {"written": ["a.md", "b.md"]}}},
    }
    monkeypatch.setattr(tools.svc, "refresh_data", lambda: raw)
    out = tools.call("refresh_data")
    assert out["hashtag_import"] == {
        "run_id": "r",
        "healthy": False,
        "health_reasons": ["x"],
        "pages_ok": 1,
        "problems": {"league_sync": "league_mismatch: who"},
    }
    assert len(out["rostered_injury_changes"]) == 10 and out["rostered_injury_changes_total"] == 15
    assert "id" not in out["rostered_injury_changes"][0] and len(out["rostered_injury_changes"][0]["comment"]) < 110
    assert out["yahoo_snapshot"]["transactions"]["memory"] == {"written": 2}
    assert tools.call("refresh_data", {"verbose": "true"}) == raw


def test_weekly_retro_data_default_is_compact_and_section_is_full(monkeypatch):
    decisions = [
        {"ref": f"P-{i}", "kind": "waiver_claim", "title": "t", "grade": "good", "delta": i, "big": "x" * 500}
        for i in range(20)
    ]
    data = {"week_et": "2026-11-02", "decisions": decisions, "regret_by_kind": {"a": 1}}
    monkeypatch.setattr(tools.svc, "weekly_retro_data_tool", lambda week_start=None: data)
    out = tools.call("weekly_retro_data")
    assert out["decisions_total"] == 20 and len(out["decisions"]) == 10 and "big" not in out["decisions"][0]
    assert out["decisions"][-1]["ref"] == "P-19" and out["sections"]["decisions"] > 10000
    assert out["regret_by_kind"] == {"a": 1}
    full = tools.call("weekly_retro_data", {"section": "decisions", "limit": "2"})
    assert (
        full["total"] == 20
        and [d["ref"] for d in full["decisions"]] == ["P-18", "P-19"]
        and "big" in full["decisions"][0]
    )
    assert "unknown section" in tools.call("weekly_retro_data", {"section": "nope"})["error"]
