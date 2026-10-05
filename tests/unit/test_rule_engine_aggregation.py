"""ISSUE-1.5 R/C 聚合 + 拓扑排序 + 结果组装单测（需求 §4.3/§4.4/§4.5）。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config_loader import load_templates
from core.expr import build_parameter_context
from core.models import TriState
from core.rule_engine import RuleEngine, aggregate_vote, aggregate_weighted
from core.validator import InputValidator

TEMPLATE_ID = "oil_fracturing_v1"
T, F, U = TriState.TRUE, TriState.FALSE, TriState.UNKNOWN


@pytest.fixture()
def cfg(templates_root: Path):  # noqa: ANN001
    return load_templates(templates_root)[TEMPLATE_ID]


def engine_for(cfg, request: dict | None = None) -> RuleEngine:  # noqa: ANN001
    return RuleEngine(cfg.nodes, build_parameter_context(cfg, request or {}))


# ── 拓扑排序（children 先于 parent）────────────────────
def test_toposort_children_before_parents(cfg) -> None:  # noqa: ANN001
    engine = engine_for(cfg)
    order = engine._toposort()
    pos = {code: i for i, code in enumerate(order)}
    assert pos["P001"] < pos["R06"] < pos["C015"]        # P → R → C 逐级在前
    assert pos["R01"] < pos["C001"]                       # R01 在根 C001 之前
    assert len(order) == len(cfg.nodes)                   # 全节点入序


def test_toposort_detects_cycle(cfg) -> None:  # noqa: ANN001
    cyclic = {
        "A": SimpleNamespace(type="rule", aggregate="AND", children=["B"]),
        "B": SimpleNamespace(type="rule", aggregate="AND", children=["A"]),
    }
    engine = RuleEngine(cyclic, build_parameter_context(cfg))
    with pytest.raises(ValueError, match="环"):
        engine._toposort()


def test_toposort_self_loop(cfg) -> None:  # noqa: ANN001
    loop = {"A": SimpleNamespace(type="rule", aggregate="AND", children=["A"])}
    engine = RuleEngine(loop, build_parameter_context(cfg))
    with pytest.raises(ValueError):
        engine._toposort()


# ── 聚合算子（AND/OR/UNKNOWN 传播）─────────────────────
def test_r11_and_requires_all_low(cfg) -> None:  # noqa: ANN001
    # 新规则（2026-10-05）：R 节点来源一律 AND → R11 = AND(P006..P009)
    engine = engine_for(cfg)
    states = engine.evaluate_states({"P006": {"oil_diff": 20}})   # 仅 P006 真，其余缺→UNKNOWN
    assert states["P006"] == T
    assert states["R11"] == U                # AND 见 UNKNOWN 不短路（无 FALSE 可短路）
    # 四叶全真才 TRUE
    states = engine.evaluate_states({
        "P006": {"oil_diff": 20}, "P007": {"liquid_intensity": 1},
        "P008": {"liquid_daily": 5}, "P009": {"oil_daily": 2},
    })
    assert states["R11"] == T
    # 任一 FALSE 即短路假
    assert engine._aggregate(cfg.nodes["R11"], [F, T, T, T]) == F


def test_rc_aggregate_follows_new_business_rule(cfg) -> None:  # noqa: ANN001
    """守护 2026-10-05 拍板规则：真实模板中所有 R 来源 AND、所有 C 来源 OR。"""
    rules = {
        code: getattr(node, "aggregate", None) or "AND"
        for code, node in cfg.nodes.items()
        if getattr(node, "type", None) in ("rule", "conclusion")
    }
    assert [c for c, a in rules.items() if c.startswith("R") and a.upper() != "AND"] == []
    assert [c for c, a in rules.items() if c.startswith("C") and a.upper() != "OR"] == []


def test_c020_or(cfg) -> None:  # noqa: ANN001
    engine = engine_for(cfg, {"basis": {"block_avg_pressure": 16}})
    states = engine.evaluate_states({"P010": {"formation_pressure": 18}})  # P010 真
    assert states["R12-1"] == T
    assert states["C020"] == T               # OR 真


def test_r02_2_and_shortcircuit_false(cfg) -> None:  # noqa: ANN001
    # R02-2 = AND(C015, P003)；P003 判假（9 > 9 不成立）→ AND FALSE
    engine = engine_for(cfg, {"basis": {"block_avg_oil_daily": 6}})
    states = engine.evaluate_states({
        "P001": {"reservoir_layers": 5, "converted_thickness": 12},
        "P002": {"total_connected_thickness": 30},
        "P003": {"neighbor_avg_oil_daily": 9.0},
    })
    assert states["P003"] == F
    assert states["R02-2"] == F


def test_unknown_propagates_without_shortcircuit(cfg) -> None:  # noqa: ANN001
    # 全部缺输入：R06 = AND(P001,P002) 二者 UNKNOWN → UNKNOWN
    engine = engine_for(cfg)
    states = engine.evaluate_states({})
    assert states["P001"] == U and states["P002"] == U
    assert states["R06"] == U
    assert states["C001"] == U               # 根在无输入时 UNKNOWN


# ── VOTE / WEIGHTED 骨架 ───────────────────────────────
@pytest.mark.parametrize(
    ("states", "expected"),
    [([T, T, F], T), ([F, F, T], F), ([T, F], U), ([U, U], U), ([T, U, F, F], F)],
)
def test_aggregate_vote(states, expected) -> None:  # noqa: ANN001
    assert aggregate_vote(states) == expected


def test_aggregate_weighted_three_outcomes() -> None:
    # quota=6：TRUE 权重达 6 → TRUE；不足且 UNKNOWN 也补不上 → FALSE；介于其间 → UNKNOWN
    assert aggregate_weighted([T, T, F], [4.0, 3.0, 2.0], 6.0) == T   # 7>=6
    assert aggregate_weighted([F, F, U], [4.0, 3.0, 1.0], 6.0) == F   # 0+1<6
    assert aggregate_weighted([T, U, F], [4.0, 3.0, 2.0], 6.0) == U   # 4>=6? no; 4+3<6? no → U


def test_weighted_requires_quota(cfg) -> None:  # noqa: ANN001
    node = SimpleNamespace(type="rule", aggregate="WEIGHTED", children=["X"], quota=None)
    engine = RuleEngine({"A": node}, build_parameter_context(cfg))
    with pytest.raises(ValueError, match="quota"):
        engine._aggregate(node, [T])


def test_aggregate_dispatch_vote_via_engine(cfg) -> None:  # noqa: ANN001
    # 验证“算子来自 rules.yaml”：aggregate=VOTE 时走 aggregate_vote
    node = SimpleNamespace(type="rule", aggregate="VOTE", children=["P1", "P2", "P3"])
    engine = RuleEngine({"A": node}, build_parameter_context(cfg))
    assert engine._aggregate(node, [T, T, F]) == T
    assert engine._aggregate(node, [T, F, U]) == U        # 已确定平票→UNKNOWN


def test_aggregate_dispatch_weighted_with_weights(cfg) -> None:  # noqa: ANN001
    # dict 权重与 list 权重均能对齐 children，驱动 aggregate_weighted
    node_dict = SimpleNamespace(
        type="rule", aggregate="WEIGHTED", children=["X", "Y", "Z"],
        weights={"X": 5, "Y": 3, "Z": 1}, quota=6.0,
    )
    engine = RuleEngine({"A": node_dict}, build_parameter_context(cfg))
    assert engine._aggregate(node_dict, [T, T, F]) == T     # 5+3>=6
    node_list = SimpleNamespace(
        type="rule", aggregate="WEIGHTED", children=["X", "Y"],
        weights=[2.0, 2.0], quota=6.0,
    )
    assert engine._aggregate(node_list, [T, U]) == F        # 2+2<6


# ── EvaluationResult 组装 + 根结论 ─────────────────────
def test_evaluate_builds_result_with_root(cfg) -> None:  # noqa: ANN001
    engine = engine_for(cfg)
    result = engine.evaluate({}, template_id=TEMPLATE_ID, well_id="W001", rules_version="2026.04")
    assert result.root_state == U
    assert result.template_id == TEMPLATE_ID
    assert result.well_id == "W001"
    assert result.rules_version == "2026.04"
    # node_states 覆盖 rules.yaml 全部业务码
    assert set(result.node_states) == set(cfg.nodes)


# ── 黄金样例全链路：三态输出与预期快照一致 ─────────────
def test_golden_sample_full_snapshot(cfg, templates_root: Path) -> None:  # noqa: ANN001
    sample = json.loads(
        (templates_root / TEMPLATE_ID / "sample_request.json").read_text(encoding="utf-8")
    )
    engine = engine_for(cfg, sample)
    nv = InputValidator.from_config(cfg).validate_node_values(sample["node_values"]).values
    states = engine.evaluate_states(nv)

    # 全链路无任何 UNKNOWN（数据齐备）
    assert all(s in (T, F) for s in states.values()), \
        {c: s.value for c, s in states.items() if s == U}

    # 决定性快照（新规则 R=AND/C=OR，2026-10-05）：
    #   P008 假沿 R11(AND)→C019(OR透传)→R03(AND)→C012→R01→C001 传导至根；
    #   P003 假仍在 R02-2(AND) 短路，但 C011=OR 见 R02-1 真即翻 TRUE。
    expected = {
        "P001": T, "P002": T, "P003": F, "P005": T, "P006": T, "P007": T,
        "P008": F, "P009": T, "P010": T, "P011": T, "P012": F, "P013": T, "P015": T, "P016": T,
        "R11": F, "R12-1": T, "R12-2": T, "R12-3": F, "R19": T, "R06": T, "R10": T, "R14": T, "R15": T,
        "R02-1": T, "R02-2": F, "C015": T, "C018": T, "C011": T, "C020": T,
        "C019": F, "R03": F, "C012": F, "C013": T, "R05": T, "C022": T, "C023": T,
        "C014": T, "R01": F, "C001": F,
    }
    for code, want in expected.items():
        assert states[code] == want, f"{code}: 期望 {want.value} 实得 {states[code].value}"

    # 根结论 C001 = FALSE（P008 未达标经 R 的 AND 链传导；C 的 OR 不能捞回单链假）
    result = engine.evaluate(nv, template_id=TEMPLATE_ID, well_id="W001")
    assert result.root_state == F
