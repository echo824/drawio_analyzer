"""渲染结果样式双通道单测（需求 §6.1–6.3；2026-10-08 通道调整）。

fill 通道（旧，底色绿/红/黄）保留向后兼容；stroke 通道（新）= 换 strokeColor +
加粗，节点背景色保留模板原含义不被动。v1 模板现走 stroke：基于 cfg 的用例断言 stroke 效果。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.models import EvaluationResult, TriState
from core.renderer import (
    Renderer,
    apply_bold,
    apply_fill_color,
    apply_stroke_color,
    apply_stroke_width,
)
from core.rule_engine import RuleEngine
from core.validator import InputValidator

TEMPLATE_ID = "oil_fracturing_v1"
T, F, U = TriState.TRUE, TriState.FALSE, TriState.UNKNOWN


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
def sample(templates_root: Path) -> dict:  # noqa: ANN001
    return json.loads((templates_root / TEMPLATE_ID / "sample_request.json").read_text(encoding="utf-8"))


def eval_states(cfg, model, sample):  # noqa: ANN001
    nv = InputValidator.from_config(cfg).validate_node_values(sample["node_values"]).values
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg, sample))
    return engine.evaluate(nv, template_id=TEMPLATE_ID, well_id="W001")


# ── 样式合并算法（§6.3 通用键替换）────────────────────────
def test_replaces_fillColor_and_preserves_other_keys() -> None:
    style = "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#FFCCFF;strokeColor=#000000;"
    out = apply_fill_color(style, "#00B050")
    assert out == "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#00B050;strokeColor=#000000;"
    assert "strokeColor=#000000" in out and "html=1" in out   # 其余键原样


def test_appends_fillColor_when_absent() -> None:
    assert apply_fill_color("rounded=0;orthogonalLoop=1;", "#FFFF00") == \
        "rounded=0;orthogonalLoop=1;fillColor=#FFFF00;"


def test_stroke_channel_only_touches_strokeColor() -> None:
    style = "ellipse;whiteSpace=wrap;fillColor=#FFFFCC;strokeColor=#000000;fontSize=12;"
    out = apply_stroke_color(style, "#FF0000")
    assert out == "ellipse;whiteSpace=wrap;fillColor=#FFFFCC;strokeColor=#FF0000;fontSize=12;"


# ── 边线粗细 strokeWidth（2026-10-08 强化）──────────────
def test_stroke_width_appends_when_absent_and_preserves_others() -> None:
    style = "ellipse;whiteSpace=wrap;fillColor=#FFFFCC;strokeColor=#000000;"
    out = apply_stroke_width(style, 3)
    assert "strokeWidth=3" in out
    assert out.startswith("ellipse;") and "fillColor=#FFFFCC" in out and "strokeColor=#000000" in out


def test_stroke_width_idempotent_and_clamps() -> None:
    once = apply_stroke_width("rounded=0;", 3)
    assert apply_stroke_width(once, 3) == once            # 同值不改写
    assert "strokeWidth=1" in apply_stroke_width("rounded=0;", 0)   # <1 兜底 1
    assert "strokeWidth=1" in apply_stroke_width("rounded=0;", "x")  # 非数兜底 1


# ── 加粗（fontStyle 按位或 1）─────────────────────────────
def test_bold_appends_when_fontStyle_absent() -> None:
    assert apply_bold("rounded=0;whiteSpace=wrap;") == "rounded=0;whiteSpace=wrap;fontStyle=1;"


def test_bold_preserves_other_bits_and_idempotent() -> None:
    once = apply_bold("rounded=0;fontStyle=4;")      # 4=下划线 → 5=下划线+加粗
    assert once == "rounded=0;fontStyle=5;"
    assert apply_bold(once) == once                  # 已含粗位不再变


# ── result_color：stroke 通道按边线色取结果 ───────────────
def test_result_color_three_states(renderer: Renderer) -> None:
    assert renderer.channel == "stroke"
    assert renderer.result_color(T) == "#00B050"
    assert renderer.result_color(F) == "#FF0000"
    assert renderer.result_color(U) == "#FFFF00"
    assert renderer.fill_for(T) is None              # stroke 定义块无 fillColor


# ── colorize 补丁：真实模型 + 黄金求值（stroke 通道）─────
def test_colorize_patches_only_p_and_c(cfg, model, renderer, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    kind_by_code = {n.code: n.kind for n in model.nodes if n.code}
    id_to_code = {cid: c for c, cid in model.code_map.items()}
    patched = {id_to_code[cid] for cid in patches}
    pc_codes = {c for c, k in kind_by_code.items() if k in ("P", "C")}
    r_codes = {c for c, k in kind_by_code.items() if k == "R"}
    assert patched == pc_codes
    assert len(patches) == len(pc_codes) == 25
    assert not any(model.code_map[c] in patches for c in r_codes)


def test_colorize_stroke_and_bold_keeps_fill_untouched(cfg, model, renderer, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    # C001=FALSE→红边加粗，背景恢复模板原义（#FFCCFF 不再被结果覆盖）
    assert result.node_states["C001"] == F
    c001 = patches[model.code_map["C001"]]
    assert "strokeColor=#FF0000" in c001 and "fontStyle=1" in c001
    assert "strokeWidth=3" in c001                             # 边线加粗到 3
    assert "fillColor=#FFCCFF" in c001
    # C013=TRUE→绿边；R06 不施样式
    assert "strokeColor=#00B050" in patches[model.code_map["C013"]]
    assert model.code_map["R06"] not in patches


def test_unknown_stroke_yellow_not_polluting_fill(cfg, model, renderer) -> None:  # noqa: ANN001
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg))     # 无输入 → 全 UNKNOWN
    result = engine.evaluate({}, template_id=TEMPLATE_ID)
    patches = renderer.colorize(model, result)
    p001 = patches[model.code_map["P001"]]
    assert "strokeColor=#FFFF00" in p001
    # 模板淡黄底保持（该节点源码写作 light-dark 暗色模式对，小写形态，未被结果样式覆盖）
    assert "light-dark(#ffffcc" in p001.lower()


def test_unknown_omitted_when_spec_blank(cfg, model, sample) -> None:  # noqa: ANN001
    empty = Renderer({"result_style": {"channel": "stroke", "stroke_width": 1, "TRUE": {}}})
    result = eval_states(cfg, model, sample)
    assert empty.colorize(model, result) == {}                       # 无色无粗宽度=1 → 幂等零补丁


# ── fill 通道向后兼容（显式旧配置仍可用）─────────────────
def test_fill_channel_still_supported(cfg, model, sample) -> None:  # noqa: ANN001
    legacy = Renderer({"result_style": {"color_kinds": ["P"],
                                        "TRUE": {"fillColor": "#00B050"},
                                        "FALSE": {"fillColor": "#FF0000"},
                                        "UNKNOWN": {"fillColor": "#FFFF00"}}})
    assert legacy.channel == "fill"                                  # 缺省即旧通道
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg))
    result = engine.evaluate({}, template_id=TEMPLATE_ID)
    p001 = legacy.colorize(model, result)[model.code_map["P001"]]
    assert "fillColor=#FFFF00" in p001                               # 旧行为不变


# ── 透传与只读保障 ────────────────────────────────────────
def test_unmatched_nodes_passthrough_not_patched(renderer: Renderer, model) -> None:  # noqa: ANN001
    empty = EvaluationResult(template_id=TEMPLATE_ID, well_id=None, rules_version=None, node_states={})
    assert renderer.colorize(model, empty) == {}


def test_missing_state_color_skipped(cfg, model, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    bare = Renderer({"result_style": {"channel": "fill"}})          # 无任何状态样式
    assert bare.colorize(model, result) == {}


def test_colorize_drawio_applies_and_keeps_source_immutable(cfg, renderer, model, flow_path, sample) -> None:  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    result = eval_states(cfg, model, sample)
    xml_out = renderer.colorize_drawio(flow_path, model, result)
    cell_id = model.code_map["C001"]
    seg = next(s for s in xml_out.split("<mxCell ") if f'id="{cell_id}"' in s)
    assert "strokeColor=#FF0000" in seg and "fontStyle=1" in seg
    assert flow_path.read_text(encoding="utf-8") == before          # 原始 .drawio 未被改写


def test_apply_to_xml_counts_changes(cfg, renderer, model, sample) -> None:  # noqa: ANN001
    import defusedxml.ElementTree as SafeET
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    root = SafeET.parse(str(cfg.path / "flow.drawio")).getroot()
    assert renderer.apply_to_xml(root, patches) == len(patches)
