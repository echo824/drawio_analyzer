"""ISSUE-1.4 P 节点三态求值单测（需求 §4.2/§4.4/§4.5）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config_loader import load_templates
from core.expr import ParameterContext, build_parameter_context
from core.models import TriState
from core.rule_engine import RuleEngine, compare, merge

TEMPLATE_ID = "oil_fracturing_v1"
T, F, U = TriState.TRUE, TriState.FALSE, TriState.UNKNOWN


@pytest.fixture()
def cfg(templates_root: Path):  # noqa: ANN201
    return load_templates(templates_root)[TEMPLATE_ID]


def make_engine(cfg, request: dict | None = None) -> RuleEngine:  # noqa: ANN001
    ctx = build_parameter_context(cfg, request or {})
    return RuleEngine.from_config(cfg, ctx)


# ── compare 单元 ─────────────────────────────────────────
@pytest.mark.parametrize(
    ("left", "op", "right", "expected"),
    [
        (40, "<", 70, T), (80, "<", 70, F),
        (110, ">", 105, T), (100, ">", 105, F),
        (False, "==", False, T), (True, "==", False, F),
        (5, ">=", 5, T), (5, "<=", 5, T), (5, "!=", 5, F), (6, "!=", 5, T),
    ],
)
def test_compare(left, op, right, expected) -> None:  # noqa: ANN001
    assert compare(left, op, right) == expected


def test_compare_type_mismatch_is_unknown() -> None:
    assert compare("abc", ">", 5) == U          # 不可比 → UNKNOWN（不崩溃）


def test_compare_unsupported_op_raises() -> None:
    with pytest.raises(ValueError):
        compare(1, "~", 1)


# ── merge（Kleene 真值表）───────────────────────────────
@pytest.mark.parametrize(
    ("states", "logic", "expected"),
    [
        ([T, T], "AND", T), ([T, F], "AND", F), ([T, U], "AND", U),
        ([F, U], "AND", F),                        # AND 短路：FALSE 优先
        ([T, T], "OR", T), ([F, F], "OR", F), ([U, F], "OR", U),
        ([T, U], "OR", T),                         # OR 短路：TRUE 优先
        ([], "AND", U), ([], "OR", U),
        ([T, F], None, F),                         # 默认 AND
    ],
)
def test_merge(states, logic, expected) -> None:  # noqa: ANN001
    assert merge(states, logic) == expected


def test_merge_unsupported_logic_raises() -> None:
    with pytest.raises(ValueError):
        merge([T], "WEIGHTED")


# ── P005：water_cut<x13 OR <x_p005_region OR <x_p005_net（右值全面阈值化）──
def test_p005_true_via_shortcircuit(cfg) -> None:  # noqa: ANN001
    # 40 < 70 → 第一判据 TRUE，OR 短路 → TRUE
    engine = make_engine(cfg)
    assert engine.evaluate_predicate("P005", {"P005": {"water_cut": 40}}) == T


def test_p005_false(cfg) -> None:  # noqa: ANN001
    # 80<70 F；80<47(全区占位) F；80<45(井网占位) F → OR F,F,F = FALSE
    engine = make_engine(cfg)
    assert engine.evaluate_predicate("P005", {"P005": {"water_cut": 80}}) == F


def test_p005_true_via_avg_branch(cfg) -> None:  # noqa: ANN001
    # 覆盖后 60<50 F；但 60 < x_p005_region=65 → 第二判据 TRUE → OR 短路 TRUE
    engine = make_engine(cfg, {"thresholds": {"x13": 50, "x_p005_region": 65}})
    assert engine.evaluate_predicate("P005", {"P005": {"water_cut": 60}}) == T


def test_p005_unknown_when_all_thresholds_missing(cfg) -> None:  # noqa: ANN001
    # 右值阈值全部不可解析 → 各判据 UNKNOWN → OR = UNKNOWN
    engine = make_engine(cfg, {
        "thresholds": {"x13": "abc", "x_p005_region": "abc", "x_p005_net": "abc"},
    })
    assert engine.evaluate_predicate("P005", {"P005": {"water_cut": 80}}) == U


def test_p005_unknown_when_left_missing(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    assert engine.evaluate_predicate("P005", {"P005": {"water_cut": None}}) == U


# ── P010：formation_pressure > x_p010_block（区块基准阈值化）─────
def test_p010_missing_formation_pressure_is_unknown(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    assert engine.evaluate_predicate("P010", {"P010": {"formation_pressure": None}}) == U


def test_p010_missing_node_entirely_is_unknown(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    assert engine.evaluate_predicate("P010", {}) == U


def test_p010_true(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)   # x_p010_block 占位默认 16
    assert engine.evaluate_predicate("P010", {"P010": {"formation_pressure": 18}}) == T


# ── P016：casing_inner_diameter>casing_min AND casing_damage==false ──
def test_p016_boolean_true(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    nv = {"P016": {"casing_inner_diameter": 110, "casing_damage": False}}
    assert engine.evaluate_predicate("P016", nv) == T


def test_p016_boolean_false_shortcircuit(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    nv = {"P016": {"casing_inner_diameter": 110, "casing_damage": True}}  # True==False → F
    assert engine.evaluate_predicate("P016", nv) == F


def test_p016_missing_damage_is_unknown(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    nv = {"P016": {"casing_inner_diameter": 110, "casing_damage": None}}
    assert engine.evaluate_predicate("P016", nv) == U


# ── P001：两操作数 AND 合并 ─────────────────────────────
def test_p001_and_all_true(cfg) -> None:  # noqa: ANN001
    # reservoir_layers(5)>x3(3) 且 converted_thickness(12)>x4(5)
    engine = make_engine(cfg)
    nv = {"P001": {"reservoir_layers": 5, "converted_thickness": 12}}
    assert engine.evaluate_predicate("P001", nv) == T


def test_p001_and_one_false(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    nv = {"P001": {"reservoir_layers": 2, "converted_thickness": 12}}  # 2>3 F
    assert engine.evaluate_predicate("P001", nv) == F


def test_p001_and_missing_operand(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    nv = {"P001": {"reservoir_layers": 5}}  # 缺 converted_thickness → UNKNOWN
    assert engine.evaluate_predicate("P001", nv) == U


# ── 批量与守卫 ──────────────────────────────────────────
def test_evaluate_all_predicates_only_p_nodes(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    states = engine.evaluate_all_predicates({})   # 全部缺输入
    assert set(states) == {c for c, n in cfg.nodes.items() if n.type == "predicate"}
    assert all(v == U for v in states.values())   # 无输入 → 全 UNKNOWN


def test_evaluate_predicate_rejects_non_predicate(cfg) -> None:  # noqa: ANN001
    engine = make_engine(cfg)
    with pytest.raises(ValueError):
        engine.evaluate_predicate("R01", {})


# ── 直接构造 ParameterContext 冒烟 ──────────────────────
def test_engine_with_explicit_context() -> None:
    ctx = ParameterContext(symbols={"x13": 70})
    assert ctx.symbol("x13") == 70
