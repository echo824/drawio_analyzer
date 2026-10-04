"""ISSUE-1.6 fillColor 结果着色单测（需求 §6.1–6.3）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.models import TriState
from core.renderer import Renderer, apply_fill_color
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


def eval_states(cfg, model, sample) -> dict:  # noqa: ANN001, ANN001
    nv = InputValidator.from_config(cfg).validate_node_values(sample["node_values"]).values
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg, sample))
    return engine.evaluate(nv, template_id=TEMPLATE_ID, well_id="W001")


# ── apply_fill_color（§6.3 算法）────────────────────────
def test_replaces_fillColor_and_preserves_other_keys() -> None:
    style = "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#FFCCFF;strokeColor=#000000;"
    out = apply_fill_color(style, "#00B050")
    assert out == "ellipse;whiteSpace=wrap;html=1;aspect=fixed;fillColor=#00B050;strokeColor=#000000;"
    assert "strokeColor=#000000" in out and "html=1" in out   # 其余键原样


def test_appends_fillColor_when_absent() -> None:
    assert apply_fill_color("rounded=0;orthogonalLoop=1;", "#FFFF00") == \
        "rounded=0;orthogonalLoop=1;fillColor=#FFFF00;"


def test_only_fillColor_changed_case_insensitive_key_kept() -> None:
    out = apply_fill_color("fillColor=#CCFFFF;fontSize=12;", "#FF0000")
    assert out == "fillColor=#FF0000;fontSize=12;"


# ── fill_for：字符串键 result_style 映射 ────────────────
def test_fill_for_three_states(renderer: Renderer) -> None:
    assert renderer.fill_for(T) == "#00B050"
    assert renderer.fill_for(F) == "#FF0000"
    assert renderer.fill_for(U) == "#FFFF00"     # 饱和黄，区别模板淡黄 #FFFFCC


# ── colorize 补丁：真实模型 + 黄金求值 ──────────────────
def test_colorize_patches_only_p_and_c(cfg, model, renderer, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    kind_by_code = {n.code: n.kind for n in model.nodes if n.code}
    id_to_code = {cid: c for c, cid in model.code_map.items()}
    patched = {id_to_code[cid] for cid in patches}
    pc_codes = {c for c, k in kind_by_code.items() if k in ("P", "C")}
    r_codes = {c for c, k in kind_by_code.items() if k == "R"}
    # 仅 P/C 被着色（本样例全部有求值态）；R 不动
    assert patched == pc_codes
    assert len(patches) == len(pc_codes) == 24
    assert not any(model.code_map[c] in patches for c in r_codes)


def test_colorize_true_false_colors_on_real_nodes(cfg, model, renderer, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    # C001=FALSE→红；C013=TRUE→绿；保留 strokeColor；R06 不着色
    assert result.node_states["C001"] == F
    assert "#FF0000" in patches[model.code_map["C001"]]
    assert "strokeColor=#000000" in patches[model.code_map["C001"]]
    assert result.node_states["C013"] == T
    assert "#00B050" in patches[model.code_map["C013"]]
    assert model.code_map["R06"] not in patches


def test_unknown_uses_saturated_yellow(cfg, model, renderer) -> None:  # noqa: ANN001
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg))     # 无输入 → 全 UNKNOWN
    result = engine.evaluate({}, template_id=TEMPLATE_ID)
    patches = renderer.colorize(model, result)
    p001_style = patches[model.code_map["P001"]]
    assert "#FFFF00" in p001_style and "#FFFFCC" not in p001_style    # 结果黄≠模板淡黄


def test_unmatched_nodes_passthrough_not_patched(renderer: Renderer, model) -> None:  # noqa: ANN001
    # 空求值结果 → 不产生任何补丁（连线/未求值全透传）
    result = eval_states_result_empty(model)
    assert renderer.colorize(model, result) == {}


def eval_states_result_empty(model):  # noqa: ANN001, ANN201
    from core.models import EvaluationResult
    return EvaluationResult(template_id=TEMPLATE_ID, well_id=None, rules_version=None, node_states={})


def test_missing_state_color_skipped(cfg, model, sample) -> None:  # noqa: ANN001
    result = eval_states(cfg, model, sample)
    bare = Renderer({"result_style": {"channel": "fill"}})   # 无任何状态色
    assert bare.colorize(model, result) == {}


# ── 写入 XML 树 + 原始文件只读 ──────────────────────────
def test_colorize_drawio_applies_and_keeps_source_immutable(cfg, renderer, model, flow_path, sample) -> None:  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    result = eval_states(cfg, model, sample)
    xml_out = renderer.colorize_drawio(flow_path, model, result)
    # 输出串反映结果色（C001→红）
    cell_id = model.code_map["C001"]
    seg = next(s for s in xml_out.split("<mxCell ") if f'id="{cell_id}"' in s)
    assert "fillColor=#FF0000" in seg
    # 原始 .drawio 未被改写
    assert flow_path.read_text(encoding="utf-8") == before


def test_apply_to_xml_counts_changes(cfg, renderer, model, sample) -> None:  # noqa: ANN001
    import defusedxml.ElementTree as SafeET
    result = eval_states(cfg, model, sample)
    patches = renderer.colorize(model, result)
    root = SafeET.parse(str(cfg.path / "flow.drawio")).getroot()
    assert renderer.apply_to_xml(root, patches) == len(patches)
