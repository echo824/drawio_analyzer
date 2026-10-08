"""§6.6 阈值占位符替换 + §4.6 参数值回写标注 单测（二者共存）。
替换覆盖：符号→数值基本替换、括号（…）内解释文字不处理、ASCII 词边界、长符号优先防级联、
None/未定义符号原样保留、幂等、仅 P 节点参与、style 开关、原始只读。值回写覆盖：
实体(&lt;/&gt;)与中文词(大于/小于)运算符定位、裸 </>不伤 HTML 标签、多操作数各绑其一、
布尔→（是/否）、UNKNOWN 不注入、无锚点优雅跳过、幂等、与替换在同一节点共存。
"""
from __future__ import annotations

from pathlib import Path

import defusedxml.ElementTree as SafeET
import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.models import EvaluationResult, TriState
from core.renderer import (
    Renderer,
    annotate_label,
    format_annotation_value,
    substitute_label,
)

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
def sym_model(cfg, symbol_flow_path: Path):  # noqa: ANN001
    """合成符号流程图的模型（§6.6 机制集成覆盖的模板无关输入）。"""
    return DrawioParser(cfg.node_code_pattern).parse(symbol_flow_path)


# ── 纯函数：substitute_label ─────────────────────────────────
def test_basic_symbol_to_number() -> None:
    out = substitute_label("本井初期与目前日产油之差 &gt; x12 t", {"x12": 10})
    assert out == "本井初期与目前日产油之差 &gt; 10 t"


def test_number_format_raw() -> None:
    assert substitute_label("a &gt; x1 m", {"x1": 12.0}) == "a &gt; 12 m"    # 整数值浮点去小数
    assert substitute_label("a &gt; x1 m", {"x1": 12.3}) == "a &gt; 12.3 m"  # 按传入原样


def test_bracket_content_never_touched() -> None:
    """P013 型：（井距<350米）是解释性文字，括号内外同名词也不替换。"""
    lab = "与本井连通水井（井距&lt;350米）开井数 &gt; x20 口"
    out = substitute_label(lab, {"x20": 2, "350": 9, "米": 1})
    assert out == "与本井连通水井（井距&lt;350米）开井数 &gt; 2 口"


def test_paren_pairs_both_widths_and_nesting_typo() -> None:
    """P001 型：全角套半角的混用笔误，整段说明仍按"括号内"保护；x4m 单位紧随可替。"""
    lab = "折算厚度（(砂岩-有效)/3+有效） &gt; x4m"
    assert substitute_label(lab, {"x4": 5}) == lab.replace("&gt; x4m", "&gt; 5m")


def test_word_boundary() -> None:
    """右边界拦数字/下划线：x1 不伤 x13/x10；单位字母与中文紧随均可替换。"""
    out = substitute_label("x1 m 且 x13% 且 x10 t 且 x3个 且 x4m", {"x1": 5, "x13": 70, "x3": 3, "x4": 5})
    assert out == "5 m 且 70% 且 x10 t 且 3个 且 5m"


def test_longest_symbol_first_no_cascade() -> None:
    """同前缀符号单趟交替、长名优先：x_p005_region 不被 x_p005 抢先拆碎。"""
    out = substitute_label("x_p005_region与x_p005", {"x_p005": 1, "x_p005_region": 47})
    assert out == "47与1"


def test_none_and_unknown_symbol_kept() -> None:
    lab = "本井产液强度 &lt; x8 t/d.m 或 &lt; x9×全区平均值"
    assert substitute_label(lab, {"x8": 2, "x9": None}) == lab.replace("&lt; x8", "&lt; 2")


def test_idempotent_second_pass_noop() -> None:
    once = substitute_label("a &gt; x12 t", {"x12": 10})
    assert substitute_label(once, {"x12": 10}) == once    # 替换后不含符号 → 天然幂等


def test_late_stage_template_passthrough() -> None:
    """上线后的描述直写数字/数字表达式（用户例：P005/P009），机制无匹配即透传。"""
    for lab in (
        "P005 本井含水 &lt; 50%",
        "P009 本井日产油 &lt; 10000 t 或 &lt; 0.8*10000",
    ):
        assert substitute_label(lab, {"x13": 70, "x10": 3, "x1": 5}) == lab


# ── 机制集成：Renderer.substitute（以合成符号模板为输入，与生产模板解耦）──
def test_substitute_symbol_template(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    symbols = build_parameter_context(cfg).symbols       # parameters.yaml 占位默认值
    patches = renderer.substitute(sym_model, symbols)
    p005 = patches[sym_model.code_map["P005"]]
    assert "&lt; 70%" in p005 and "x13" not in p005
    assert "（全区平均值或井网平均值-Y）" in p005           # 括号内原样
    p001 = patches[sym_model.code_map["P001"]]
    assert "&gt; 3个" in p001 and "&gt; 5m" in p001          # x3/x4 均换数值
    assert "（(砂岩-有效)/3+有效）" in p001                 # 混用笔误括号仍保护
    p013 = patches[sym_model.code_map["P013"]]
    assert "（井距&lt;350米）" in p013 and "&gt; 2 口" in p013
    assert "&gt; 10 t" in patches[sym_model.code_map["P006"]]  # x12→默认 10


def test_substitute_overridden_by_request_thresholds(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    symbols = build_parameter_context(cfg, {"thresholds": {"x12": 20}}).symbols
    patches = renderer.substitute(sym_model, symbols)
    assert "&gt; 20 t" in patches[sym_model.code_map["P006"]]   # 请求覆盖 → 上线数值进图


def test_substitute_scope_only_p(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    # predicate_only：P005 描述含 x13 参与替换，C001 描述虽也含 x13 但透传不改
    patches = renderer.substitute(sym_model, {"x13": 70})
    assert sym_model.code_map["P005"] in patches
    assert sym_model.code_map["C001"] not in patches


def test_substitute_respects_disabled(sym_model) -> None:
    disabled = Renderer({"threshold_substitution": {"enabled": False}})
    assert disabled.substitute(sym_model, {"x12": 10}) == {}


def test_substitute_empty_symbols(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    assert renderer.substitute(sym_model, {}) == {}            # 无符号表 → 全图透传


# ── 真实生产模板：已数字直写 → 机制整体透传（不产生任何补丁）──
def test_substitute_real_template_is_number_hardcoded(cfg, model, renderer) -> None:  # noqa: ANN001
    symbols = build_parameter_context(cfg).symbols
    assert renderer.substitute(model, symbols) == {}           # P 描述无占位符可替换


# ── 应用到 XML：实体保留 + 原始只读 + 往返 ───────────────────
def test_substitute_drawio_readonly_and_entities(cfg, sym_model, renderer, symbol_flow_path) -> None:  # noqa: ANN001
    before = symbol_flow_path.read_text(encoding="utf-8")
    xml = renderer.substitute_drawio(symbol_flow_path, sym_model, {"x12": 20})
    assert "20 t" in xml
    assert "x12" not in xml
    assert "&amp;gt;" in xml                                # 实体经序列化仍双重编码
    assert symbol_flow_path.read_text(encoding="utf-8") == before  # 原文件不变


def test_substitute_roundtrip_decodes_back(cfg, sym_model, renderer, symbol_flow_path) -> None:  # noqa: ANN001
    xml = renderer.substitute_drawio(symbol_flow_path, sym_model, {"x12": 20})
    root = SafeET.fromstring(xml)
    cell = next(c for c in root.iter("mxCell") if c.get("id") == sym_model.code_map["P006"])
    assert cell.get("value") == "P006 本井初期与目前日产油之差 &gt; 20 t"


# ── 与 1.6 协同：着色 + 阈值替换互不干扰 ─────────────────────
def test_substitute_and_colorize_independent(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    result = EvaluationResult(
        template_id=cfg.template_id,
        well_id="W001",
        rules_version=None,
        node_states={"P006": F},
    )
    style_patches = renderer.colorize(sym_model, result)
    value_patches = renderer.substitute(sym_model, {"x12": 10})
    cid = sym_model.code_map["P006"]
    assert "strokeColor=#FF0000" in style_patches[cid]      # FALSE → 红边加粗（stroke 通道）
    assert "fillColor=#FFFFFF" in style_patches[cid]        # 模板底色不受结果样式影响
    assert "&gt; 10 t" in value_patches[cid]                # 阈值符号 → 数值


# ══════════════════════════════════════════════════════════════
# §4.6 参数值回写标注（与上方阈值替换共存）
# ══════════════════════════════════════════════════════════════
_VA = {"enabled": True, "brackets": ["（", "）"], "bool_true": "是", "bool_false": "否", "unknown": ""}


# ── 纯函数：format_annotation_value ──────────────────────────
def test_fmt_bool_and_number_and_unknown() -> None:
    assert format_annotation_value(True, _VA) == "是"       # 布尔先于数值判定
    assert format_annotation_value(False, _VA) == "否"
    assert format_annotation_value(12.0, _VA) == "12"       # 整数值浮点去小数
    assert format_annotation_value(1.2, _VA) == "1.2"
    assert format_annotation_value(None, _VA) is None        # unknown="" → 不注入
    assert format_annotation_value(None, {"unknown": "—"}) == "—"  # 配置占位则显示


# ── 纯函数：annotate_label ──────────────────────────────────
def test_annotate_entity_operator() -> None:
    lab = "P009 本井日产油 &lt; 3 t"
    assert annotate_label(lab, [("<", "2")], _VA) == "P009 本井日产油（2） &lt; 3 t"


def test_annotate_chinese_word_operator() -> None:
    """中文运算符词“小于”定位；span 标签的裸 < 不得被误伤。"""
    lab = 'P011 流压小于<span style="x">12 MPa</span>'
    out = annotate_label(lab, [("<", "10")], _VA)
    assert out == 'P011 流压（10）小于<span style="x">12 MPa</span>'


def test_annotate_multiple_operands_each_bound() -> None:
    """同节点两个 &gt; 运算符：两个量按声明顺序各绑自己的运算符。"""
    lab = "层数 &gt; 3个 且 折算厚度 &gt; 5m"
    out = annotate_label(lab, [(">", "5"), (">", "12.3")], _VA)
    assert out == "层数（5） &gt; 3个 且 折算厚度（12.3） &gt; 5m"


def test_annotate_no_anchor_graceful_skip() -> None:
    """无对应运算符（散文“不严重”）→ 优雅跳过，不追加末尾。"""
    lab = "P016 油层深度以上套管损坏<div>不严重</div>"
    assert annotate_label(lab, [("==", "否")], _VA) == lab


def test_annotate_idempotent_second_pass() -> None:
    once = annotate_label("P009 本井日产油 &lt; 3 t", [("<", "2")], _VA)
    assert annotate_label(once, [("<", "2")], _VA) == once   # 目标位已是（2） → 不重复


def test_annotate_never_matches_bare_angle_in_tags() -> None:
    """style 里的 = 与标签里的裸 < > 均不是锚点；仅实体/中文词参与。"""
    lab = '<div style="a=b">x &gt; 5</div>'
    assert annotate_label(lab, [(">", "9")], _VA) == '<div style="a=b">x（9） &gt; 5</div>'


# ── 集成：真实生产模板（数字直写）上的值回写 ────────────────
def test_value_patches_real_template_annotates(cfg, model, renderer) -> None:  # noqa: ANN001
    node_values = {"P009": {"oil_daily": 2}, "P010": {"formation_pressure": 18}}
    patches = renderer.value_patches(model, {}, cfg.nodes, node_values)
    assert patches[model.code_map["P009"]] == "P009 本井日产油（2） &lt; 3 t"
    assert "目前地层压力（18） &gt; 16 MPa" in patches[model.code_map["P010"]]


def test_value_patches_skips_r_c_and_missing(cfg, model, renderer) -> None:  # noqa: ANN001
    # R 节点不回写；UNKNOWN 量（值=None）不注入
    patches = renderer.value_patches(model, {}, cfg.nodes, {"R01": {"x": 1}, "P009": {"oil_daily": None}})
    assert model.code_map.get("R01") not in patches
    assert model.code_map["P009"] not in patches


def test_value_patches_respects_disabled(cfg, model) -> None:  # noqa: ANN001
    off = Renderer({"value_annotation": {"enabled": False}})
    assert off.value_patches(model, {}, cfg.nodes, {"P009": {"oil_daily": 2}}) == {}


def test_annotate_and_substitute_coexist_on_same_node(cfg, sym_model, renderer) -> None:  # noqa: ANN001
    """同一节点：先把 x12 换为 20（§6.6），再把实测值（15）插到运算符前（§4.6）。"""
    patches = renderer.value_patches(sym_model, {"x12": 20}, cfg.nodes, {"P006": {"oil_diff": 15}})
    assert patches[sym_model.code_map["P006"]] == "P006 本井初期与目前日产油之差（15） &gt; 20 t"


def test_value_annotation_readonly_and_roundtrip(cfg, model, renderer, flow_path) -> None:  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    xml = renderer.to_diagram_xml(
        flow_path, model, EvaluationResult(cfg.template_id, "W001", None, {}),
        nodes=cfg.nodes, node_values={"P009": {"oil_daily": 2}},
    )
    assert "本井日产油（2）" in xml
    assert "&amp;lt; 3 t" in xml                               # 实体经序列化仍双重编码
    assert flow_path.read_text(encoding="utf-8") == before    # 原文件不变
