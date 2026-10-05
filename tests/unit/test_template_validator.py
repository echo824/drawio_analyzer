"""模板校验器 V01-V24 单测（需求 §7，Phase 2 commit B）。

合成模型逐条覆盖正/负分支；并对真实模板做回归快照——已知它含 5 条悬空边
（_74/_86/_75/_77/_91，见需求 §7.2 / test_drawio_parser 的 DANGLING_IDS）与
1 处小写业务码，故 `ok is False`，用于守住校验器的检出能力不回退。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.models import Edge, Node, TemplateModel
from core.template_validator import (
    Finding,
    Level,
    TemplateValidator,
    ValidationReport,
)

# ── 合成夹具：最小可模板块 ──────────────────────────────


def _vnode(cell_id: str, code: str | None = None, label: str = "") -> Node:
    kind = code[0] if code else "UNCLASSIFIED"
    return Node(cell_id=cell_id, kind=kind, code=code, label=label or code or "",
                style="", geometry=(0, 0, 0, 0))


def _edge(cell_id: str, source: str | None, target: str | None) -> Edge:
    return Edge(cell_id=cell_id, source=source, target=target, style="")


def _model(nodes: list[Node], edges: list[Edge],
           code_map: dict[str, str], dup: set[str] | None = None) -> TemplateModel:
    return TemplateModel(nodes=nodes, edges=edges, code_map=code_map,
                         duplicate_codes=dup or set())


def _cfg(rule_nodes: dict[str, SimpleNamespace], thresholds: dict[str, object]):  # noqa: ANN202
    return SimpleNamespace(nodes=rule_nodes, thresholds=thresholds)


def _predicate(**kw: object) -> SimpleNamespace:
    return SimpleNamespace(type="predicate", children=None, **kw)


def _conclusion(children: list[str]) -> SimpleNamespace:
    return SimpleNamespace(type="conclusion", children=children, operands=None)


def _valid() -> tuple[TemplateModel, SimpleNamespace]:
    """一个全部通过 V01-V23 的最小合规块。"""
    nodes = [_vnode("n1", "P001"), _vnode("n2", "C001")]
    edges = [_edge("e1", "n1", "n2")]
    code_map = {"P001": "n1", "C001": "n2"}
    model = _model(nodes, edges, code_map)
    cfg = _cfg(
        {
            "P001": _predicate(operands=[{"name": "x", "op": ">", "threshold": "t1"}]),
            "C001": _conclusion(["P001"]),
        },
        {"t1": {"value": 1}},
    )
    return model, cfg


validate = TemplateValidator().validate


def codes(report: ValidationReport) -> set[str]:
    return {f.code for f in report.findings}


# ── 数据结构（沿用 Phase 2 占位期的构造测试）─────────────

def test_level_is_str_enum() -> None:
    assert Level.ERROR == "error" and Level.WARNING == "warning"
    assert Level("warning") is Level.WARNING


def test_finding_slots_and_default_node() -> None:
    f = Finding("V01", Level.ERROR, "dup")
    assert f.node is None
    assert not hasattr(f, "__dict__")
    with pytest.raises(AttributeError):
        f.extra = 1


def test_report_ok_and_accessors() -> None:
    rep = ValidationReport()
    assert rep.ok and rep.errors() == [] and rep.warnings() == []
    rep.findings.append(Finding("V13", Level.WARNING, "w"))
    assert rep.ok and len(rep.warnings()) == 1
    rep.findings.append(Finding("V01", Level.ERROR, "e"))
    assert not rep.ok and len(rep.errors()) == 1


# ── 合规块：无 finding、ok ──────────────────────────────

def test_valid_block_has_no_findings() -> None:
    model, cfg = _valid()
    report = validate(model, cfg)
    assert report.ok
    assert report.findings == []


# ── V01 业务码唯一 ──────────────────────────────────────

def test_v01_duplicate_code_is_error() -> None:
    model, cfg = _valid()
    model.duplicate_codes = {"P001"}
    report = validate(model, cfg)
    assert not report.ok
    assert any(f.code == "V01" and f.node == "P001" for f in report.errors())


# ── V02 大小写归一 ──────────────────────────────────────

def test_v02_lowercase_code_is_warning() -> None:
    model, cfg = _valid()
    model.nodes[0].label = "p003 低产液"       # 原始小写
    model.code_map["P001"] = "n1"
    report = validate(model, cfg)
    assert report.ok                             # 仅告警
    assert any(f.code == "V02" and f.level is Level.WARNING for f in report.findings)


# ── V10 悬空边 ──────────────────────────────────────────

def test_v10_missing_endpoint_is_error() -> None:
    model, cfg = _valid()
    model.edges.append(_edge("e_dangling", None, "n2"))
    report = validate(model, cfg)
    assert not report.ok
    assert any(f.code == "V10" and f.node == "e_dangling" for f in report.errors())


# ── V11 端点指向存在节点 ────────────────────────────────

def test_v11_endpoint_points_to_missing_node() -> None:
    model, cfg = _valid()
    model.edges[0] = _edge("e1", "n1", "ghost")   # ghost 不存在
    report = validate(model, cfg)
    assert not report.ok
    assert any(f.code == "V11" for f in report.errors())


# ── V12 依赖无环 ────────────────────────────────────────

def test_v12_cycle_is_error() -> None:
    model, _ = _valid()
    cfg = _cfg(
        {"P001": _conclusion(["C001"]), "C001": _conclusion(["P001"])},
        {},
    )
    report = validate(model, cfg)
    assert not report.ok
    assert any(f.code == "V12" for f in report.errors())


# ── V13 孤立节点 ────────────────────────────────────────

def test_v13_isolated_node_is_warning() -> None:
    model, cfg = _valid()
    model.edges = []                               # 去掉全部连线 → 两端皆孤立
    report = validate(model, cfg)
    assert report.ok                               # 仅告警
    isolated = {f.node for f in report.findings if f.code == "V13"}
    assert isolated == {"P001", "C001"}


# ── V20 图→规则一致 ─────────────────────────────────────

def test_v20_graph_code_without_rule() -> None:
    model, cfg = _valid()
    model.code_map["P999"] = "n9"                  # 图里有、规则里没有
    model.nodes.append(_vnode("n9", "P999"))
    model.edges.append(_edge("e9", "n9", "n1"))
    report = validate(model, cfg)
    assert any(f.code == "V20" and f.node == "P999" for f in report.errors())


# ── V21 规则→图一致 ─────────────────────────────────────

def test_v21_rule_code_without_graph_node() -> None:
    model, cfg = _valid()
    cfg.nodes["P050"] = _predicate(operands=[])    # 规则里有、图里没有
    report = validate(model, cfg)
    assert any(f.code == "V21" and f.node == "P050" for f in report.errors())


# ── V22 children 引用存在 ───────────────────────────────

def test_v22_child_reference_missing() -> None:
    model, cfg = _valid()
    cfg.nodes["C001"] = _conclusion(["P001", "GHOST"])
    report = validate(model, cfg)
    assert any(f.code == "V22" and f.node == "C001" for f in report.errors())


# ── V23 阈值符号已定义 ──────────────────────────────────

def test_v23_undefined_threshold_symbol() -> None:
    model, cfg = _valid()
    cfg.nodes["P001"] = _predicate(operands=[{"name": "x", "op": ">", "threshold": "t_missing"}])
    report = validate(model, cfg)
    assert any(f.code == "V23" and "t_missing" in f.message for f in report.errors())


def test_v23_undefined_symbol_inside_expr() -> None:
    model, cfg = _valid()
    cfg.nodes["P001"] = _predicate(
        operands=[{"name": "x", "op": ">", "expr": "@avg_oil * k_undefined"}]
    )
    report = validate(model, cfg)
    assert any(f.code == "V23" and "k_undefined" in f.message for f in report.errors())


def test_v23_defined_symbols_pass() -> None:
    model, cfg = _valid()
    cfg.nodes["P001"] = _predicate(
        operands=[{"name": "x", "op": ">", "expr": "@avg_oil * t1"}]  # t1 已定义, @avg_oil 属 basis
    )
    report = validate(model, cfg)
    assert not any(f.code == "V23" for f in report.findings)


# ── V24 接口位（暂缓）───────────────────────────────────

def test_v24_is_stub_returning_no_findings() -> None:
    model, cfg = _valid()
    cfg.nodes["P001"] = _predicate(operands=[{"name": "x", "op": ">", "expr": "@undeclared_basis"}])
    report = validate(model, cfg)
    assert "V24" not in codes(report)              # 暂不产出 V24


# ── 真实模板回归快照：已知缺陷必须被检出 ────────────────

def test_real_template_detects_known_dangling_edges(templates_root: Path) -> None:
    cfg = load_templates(templates_root)["oil_fracturing_v1"]
    model = DrawioParser(cfg.node_code_pattern).parse(cfg.path / "flow.drawio")
    report = validate(model, cfg)

    dangling = {f.node for f in report.findings if f.code == "V10"}
    assert dangling == {
        "ukcuZfHQnJ5xVRSnUBC_-74", "ukcuZfHQnJ5xVRSnUBC_-86", "ukcuZfHQnJ5xVRSnUBC_-75",
        "ukcuZfHQnJ5xVRSnUBC_-77", "ukcuZfHQnJ5xVRSnUBC_-91",
    }
    assert not report.ok                           # 悬空边为错误级
    # 一致性/拓扑/阈值类不应误报
    assert codes(report) == {"V10", "V02"}
