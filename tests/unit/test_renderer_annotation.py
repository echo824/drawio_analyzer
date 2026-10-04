"""ISSUE-1.7 单测：参数值回写标注（§6.6）。
覆盖：全角括号、运算符前定位、命名槽位顺序、布尔是/否、UNKNOWN 不插、
幂等、仅追加不改原文、实体/富文本保留、R/C 不回写、原始只读。
"""
from __future__ import annotations

from pathlib import Path

import defusedxml.ElementTree as SafeET
import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.models import EvaluationResult, TriState
from core.renderer import Renderer, annotate_label, format_annotation_value

TEMPLATE_ID = "oil_fracturing_v1"
F = TriState.FALSE


@pytest.fixture()
def cfg(templates_root: Path):  # noqa: ANN001
    return load_templates(templates_root)[TEMPLATE_ID]


@pytest.fixture()
def flow_path(templates_root: Path) -> Path:
    return templates_root / TEMPLATE_ID / "flow.drawio"


@pytest.fixture()
def model(cfg, flow_path: Path):  # noqa: ANN001
    return DrawioParser(cfg.node_code_pattern).parse(flow_path)


@pytest.fixture()
def renderer(cfg) -> Renderer:  # noqa: ANN001
    return Renderer(cfg.style)


@pytest.fixture()
def va(cfg) -> dict:  # noqa: ANN001
    return cfg.style["value_annotation"]


# ── 纯格式化 ─────────────────────────────────────────────────
def test_format_bool_number_unknown(va) -> None:  # noqa: ANN001
    assert format_annotation_value(True, va) == "是"
    assert format_annotation_value(False, va) == "否"
    assert format_annotation_value(5, va) == "5"
    assert format_annotation_value(12.0, va) == "12"       # 整数值去小数
    assert format_annotation_value(12.3, va) == "12.3"
    assert format_annotation_value(None, va) is None        # UNKNOWN 默认不显示


# ── 运算符前定位（验收基准用例）─────────────────────────────
def test_p006_single_condition_insert_before_operator(va) -> None:  # noqa: ANN001
    out = annotate_label("本井初期与目前日产油之差 &gt; x12 t", [(">", "20")], va)
    assert out == "本井初期与目前日产油之差（20） &gt; x12 t"


def test_p001_multi_condition_named_slots(va) -> None:  # noqa: ANN001
    lab = "可压储层层数 &gt; x3个 且 折算厚度 &gt; x4m"
    out = annotate_label(lab, [(">", "5"), (">", "12")], va)
    assert out == "可压储层层数（5） &gt; x3个 且 折算厚度（12） &gt; x4m"


def test_idempotent_no_double_paren(va) -> None:  # noqa: ANN001
    lab = "本井初期与目前日产油之差 &gt; x12 t"
    once = annotate_label(lab, [(">", "20")], va)
    twice = annotate_label(once, [(">", "20")], va)
    assert twice == once                                    # 重复渲染不叠加


def test_unknown_binding_not_inserted(va) -> None:  # noqa: ANN001
    assert annotate_label("a &gt; b", [], va) == "a &gt; b"  # 无绑定 → 原样


# ── 真实模板集成 ─────────────────────────────────────────────
def test_annotate_real_p006_and_p001(cfg, model, renderer) -> None:  # noqa: ANN001
    patches = renderer.annotate(
        cfg.nodes,
        {"P006": {"oil_diff": 20}, "P001": {"reservoir_layers": 5, "converted_thickness": 12.3}},
        model,
    )
    p006 = patches[model.code_map["P006"]]
    assert "之差（20） &gt; x12" in p006
    p001 = patches[model.code_map["P001"]]
    assert "可压储层层数（5） &gt; x3" in p001
    assert "有效）（12.3） &gt; x4m" in p001                # 既有括号保留，注释紧随其后


def test_annotate_boolean_and_no_operator_fallback(cfg, model, renderer) -> None:  # noqa: ANN001
    patches = renderer.annotate(
        cfg.nodes,
        {"P016": {"casing_inner_diameter": 110, "casing_damage": False}},
        model,
    )
    lab = patches[model.code_map["P016"]]
    assert "套管通径（110）&gt;105mm" in lab               # 匹配 > 运算符前
    assert lab.endswith("（否）")                          # == 无运算符 → 末尾追加（否）


def test_annotate_skips_missing_and_rules(cfg, model, renderer) -> None:  # noqa: ANN001
    patches = renderer.annotate(
        cfg.nodes,
        {"P005": {"water_cut": None}, "R01": {"x": 1}},     # P005 UNKNOWN、R01 非输入节点
        model,
    )
    assert patches == {}                                    # 二者都不回写


def test_annotate_respects_disabled(cfg, model) -> None:  # noqa: ANN001
    disabled = Renderer({"value_annotation": {"enabled": False}})
    assert disabled.annotate(cfg.nodes, {"P006": {"oil_diff": 20}}, model) == {}


def test_annotate_only_input_nodes(cfg, model, renderer) -> None:  # noqa: ANN001
    patches = renderer.annotate(cfg.nodes, {"P006": {"oil_diff": 20}}, model)
    assert model.code_map["P001"] not in patches            # 不在输入的 P 不动


# ── 回写应用到 XML：实体保留 + 原始只读 ─────────────────────
def test_annotate_drawio_readonly_and_entities(cfg, model, renderer, flow_path) -> None:  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    xml = renderer.annotate_drawio(flow_path, cfg.nodes, {"P006": {"oil_diff": 20}}, model)
    assert "（20）" in xml
    assert "&amp;gt;" in xml                                # 实体经序列化仍双重编码
    assert flow_path.read_text(encoding="utf-8") == before  # 原文件不变


def test_annotate_roundtrip_decodes_back(cfg, model, renderer, flow_path) -> None:  # noqa: ANN001
    xml = renderer.annotate_drawio(flow_path, cfg.nodes, {"P006": {"oil_diff": 20}}, model)
    root = SafeET.fromstring(xml)
    cell = next(c for c in root.iter("mxCell") if c.get("id") == model.code_map["P006"])
    assert cell.get("value") == "P006 本井初期与目前日产油之差（20） &gt; x12 t"


# ── 与 1.6 协同：着色 + 回写互不干扰 ────────────────────────
def test_annotate_and_colorize_independent(cfg, model, renderer) -> None:  # noqa: ANN001
    result = EvaluationResult(
        template_id=cfg.template_id,
        well_id="W001",
        rules_version=None,
        node_states={"P006": F},
    )
    style_patches = renderer.colorize(model, result)
    value_patches = renderer.annotate(cfg.nodes, {"P006": {"oil_diff": 20}}, model)
    cid = model.code_map["P006"]
    assert "fillColor=#FF0000" in style_patches[cid]        # FALSE → 红
    assert "（20）" in value_patches[cid]                    # 值回写
