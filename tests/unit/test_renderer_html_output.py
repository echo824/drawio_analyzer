"""ISSUE-1.8 单测：HTML 输出组装（§6.5、§8.2、§9.1）。
覆盖：整图 XML 叠加着色+阈值替换、结构保真、原始只读；内嵌 draw.io viewer 的 HTML
（转义内嵌可往返）；结构化 JSON；黄金样例端到端；SVG 预留。
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.renderer import VIEWER_SRC, Renderer
from core.rule_engine import RuleEngine
from core.validator import InputValidator

TEMPLATE_ID = "oil_fracturing_v1"


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
def sample(templates_root: Path) -> dict:  # noqa: ANN001
    return json.loads((templates_root / TEMPLATE_ID / "sample_request.json").read_text(encoding="utf-8"))


@pytest.fixture()
def renderer(cfg) -> Renderer:  # noqa: ANN001
    return Renderer(cfg.style)


@pytest.fixture()
def result(cfg, sample):  # noqa: ANN001
    nv = InputValidator.from_config(cfg).validate_node_values(sample["node_values"]).values
    engine = RuleEngine(cfg.nodes, build_parameter_context(cfg, sample))
    return engine.evaluate(nv, template_id=TEMPLATE_ID, well_id="W001")


# ── 整图 XML：叠加 style + 阈值替换，结构保真，原始只读 ───────
def test_diagram_xml_merges_color_and_substitution(cfg, model, renderer, flow_path):  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    xml = renderer.to_diagram_xml(
        flow_path, model, result_dummy(model), symbols={"x12": 20},
    )
    assert "20 t" in xml                                     # 阈值符号→数值（§6.6 改版）
    assert "x12" not in xml                                  # 描述中不再残留占位符
    assert "<mxfile" in xml and "<mxGraphModel" in xml       # 层级保留
    assert 'edge="1"' in xml and "source=" in xml            # 连线保留
    assert "fontSize" in xml or "strokeColor" in xml         # 原样式键保留
    assert flow_path.read_text(encoding="utf-8") == before   # 原始只读


def test_diagram_xml_colorize_only_without_substitution(cfg, model, renderer, flow_path, result):  # noqa: ANN001
    xml = renderer.to_diagram_xml(flow_path, model, result)   # 不传 symbols
    assert "#FF0000" in xml                                   # 结果色仍施加（C001 FALSE→红）
    assert "x12" in xml                                        # 无阈值替换 → 符号原样


def result_dummy(model):  # noqa: ANN001, ANN201
    from core.models import EvaluationResult, TriState
    states = {code: TriState.TRUE for code in model.code_map}
    return EvaluationResult(template_id=TEMPLATE_ID, well_id=None, rules_version=None,
                            node_states=states, root_state=TriState.TRUE)


# ── 结构化 JSON（§9.1）──────────────────────────────────────
def test_summary_dict_lists_states_colors_and_root(cfg, model, renderer, result):  # noqa: ANN001
    s = renderer.summary_dict(model, result)
    assert s["template_id"] == TEMPLATE_ID
    assert s["well_id"] == "W001"
    assert s["node_states"]["C001"] == "FALSE"
    assert s["colors"]["C001"] == "#FF0000"
    assert s["node_states"]["R06"] == "TRUE"              # 三态仍全量计算（含 R）
    assert "R06" not in s["colors"]                       # R 不着色 → 不在 colors
    assert s["colors"]["C013"] == "#00B050"              # C 结论 TRUE→绿
    assert s["root_state"] == "FALSE"
    assert len(s["node_states"]) == len(model.code_map)      # 每个业务码都有三态
    assert len(s["colors"]) == 24                            # 仅 P/C 入色


# ── HTML：内嵌 draw.io viewer，转义负载可往返 ────────────────
def test_html_embeds_viewer_and_roundtrip_xml(cfg, model, renderer, flow_path, result):  # noqa: ANN001
    xml = renderer.to_diagram_xml(flow_path, model, result)
    page = renderer.to_html(xml, title="压裂评价", summary=renderer.summary_dict(model, result))
    assert VIEWER_SRC in page                                # viewer 脚本
    assert 'class="mxgraph"' in page
    assert "&lt;mxfile" in page or "&lt;mxGraphModel" in page  # XML 以实体安全内嵌
    # 从 data-mxgraph 反解出原始 diagram XML（浏览器会先 html 解码再 JSON.parse）
    attr = re.search(r'data-mxgraph="([^"]*)"', page).group(1)
    cfg_obj = json.loads(html.unescape(attr))
    assert cfg_obj["xml"] == xml
    assert "#FF0000" in cfg_obj["xml"]                        # 结果色在负载中存活


def test_html_summary_block(renderer):  # noqa: ANN001
    page = renderer.to_html("<mxGraphModel><root/></mxGraphModel>", summary={"root_state": "FALSE"})
    assert "结构化结果 JSON" in page
    assert "&quot;root_state&quot;: &quot;FALSE&quot;" in page or "root_state" in page


# ── render 编排：返回 html/xml/summary 三件套 ────────────────
def test_render_orchestrator_returns_all_parts(cfg, model, renderer, flow_path, result):  # noqa: ANN001
    out = renderer.render(
        flow_path, model, result, symbols={"x12": 20}, title="压裂评价",
    )
    assert set(out) == {"html", "xml", "summary"}
    assert VIEWER_SRC in out["html"]
    assert "20 t" in out["xml"] and "#FF0000" in out["xml"]
    assert out["summary"]["node_states"]["C001"] == "FALSE"


# ── 黄金端到端：结果色 + 阈值替换 + 无硬编码 + 原始只读 ───────
def test_golden_sample_end_to_end(cfg, model, renderer, flow_path, sample, result):  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    symbols = build_parameter_context(cfg, sample).symbols
    out = renderer.render(flow_path, model, result, symbols=symbols)
    # 全链路无 UNKNOWN（黄金验收）
    assert all(v != "UNKNOWN" for v in out["summary"]["node_states"].values())
    assert out["summary"]["root_state"] == "FALSE"           # 最终 C001
    assert out["summary"]["node_states"]["P003"] == "FALSE"  # 由 9.0>9.0 不成立
    assert "x12" not in out["xml"] and "x13" not in out["xml"]  # 占位符已换成数值
    # 三色均来自 style（绿/红/黄），未在代码里硬编码
    assert "#00B050" in out["xml"] and "#FF0000" in out["xml"]
    assert flow_path.read_text(encoding="utf-8") == before


# ── SVG 预留 ─────────────────────────────────────────────────
def test_svg_reserved(renderer, model, result):  # noqa: ANN001
    with pytest.raises(NotImplementedError):
        renderer.render_svg(model, result)
