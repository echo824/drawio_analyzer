"""§6.6 改版单测：阈值占位符替换（2026-10 右值全面阈值化）。
覆盖：符号→数值基本替换、括号（…）内解释文字不处理、ASCII 词边界（x1 不伤 x13、
"x3个"中文紧随仍可替换）、长符号优先防级联、None/未定义符号原样保留、幂等、
数字/数字表达式后期描述透传、仅 P 节点参与（R/C 透传）、style 开关、原始只读、
与 1.6 着色互不干扰。旧"实测值插运算符前"的回写测试已随功能整体退役。
"""
from __future__ import annotations

from pathlib import Path

import defusedxml.ElementTree as SafeET
import pytest

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.models import EvaluationResult, TriState
from core.renderer import Renderer, substitute_label

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


# ── 真实模板集成：Renderer.substitute ────────────────────────
def test_substitute_real_template(cfg, model, renderer) -> None:  # noqa: ANN001
    symbols = build_parameter_context(cfg).symbols       # parameters.yaml 占位默认值
    patches = renderer.substitute(model, symbols)
    p005 = patches[model.code_map["P005"]]
    assert "&lt; 70%" in p005 and "x13" not in p005
    assert "（全区平均值或井网平均值-Y）" in p005           # 括号内原样
    p001 = patches[model.code_map["P001"]]
    assert "&gt; 3个" in p001 and "&gt; 5m" in p001          # x3/x4 均换数值
    assert "（(砂岩-有效)/3+有效）" in p001                 # 混用笔误括号仍保护
    p013 = patches[model.code_map["P013"]]
    assert "（井距&lt;350米）" in p013 and "&gt; 2 口" in p013


def test_substitute_overridden_by_request_thresholds(cfg, model, renderer) -> None:  # noqa: ANN001
    symbols = build_parameter_context(cfg, {"thresholds": {"x12": 20}}).symbols
    patches = renderer.substitute(model, symbols)
    assert "&gt; 20 t" in patches[model.code_map["P006"]]   # 请求覆盖 → 上线数值进图


def test_substitute_scope_only_p(cfg, model, renderer) -> None:  # noqa: ANN001
    patches = renderer.substitute(model, {"可压性": 9})    # 命中 R01/C001 描述的词
    assert patches == {}                                   # R/C 透传，绝不改描述


def test_substitute_respects_disabled(model) -> None:
    disabled = Renderer({"threshold_substitution": {"enabled": False}})
    assert disabled.substitute(model, {"x12": 10}) == {}


def test_substitute_empty_symbols(cfg, model, renderer) -> None:  # noqa: ANN001
    assert renderer.substitute(model, {}) == {}            # 无符号表 → 全图透传


# ── 应用到 XML：实体保留 + 原始只读 + 往返 ───────────────────
def test_substitute_drawio_readonly_and_entities(cfg, model, renderer, flow_path) -> None:  # noqa: ANN001
    before = flow_path.read_text(encoding="utf-8")
    xml = renderer.substitute_drawio(flow_path, model, {"x12": 20})
    assert "20 t" in xml
    assert "x12" not in xml
    assert "&amp;gt;" in xml                                # 实体经序列化仍双重编码
    assert flow_path.read_text(encoding="utf-8") == before  # 原文件不变


def test_substitute_roundtrip_decodes_back(cfg, model, renderer, flow_path) -> None:  # noqa: ANN001
    xml = renderer.substitute_drawio(flow_path, model, {"x12": 20})
    root = SafeET.fromstring(xml)
    cell = next(c for c in root.iter("mxCell") if c.get("id") == model.code_map["P006"])
    assert cell.get("value") == "P006 本井初期与目前日产油之差 &gt; 20 t"


# ── 与 1.6 协同：着色 + 阈值替换互不干扰 ─────────────────────
def test_substitute_and_colorize_independent(cfg, model, renderer) -> None:  # noqa: ANN001
    result = EvaluationResult(
        template_id=cfg.template_id,
        well_id="W001",
        rules_version=None,
        node_states={"P006": F},
    )
    style_patches = renderer.colorize(model, result)
    value_patches = renderer.substitute(model, {"x12": 10})
    cid = model.code_map["P006"]
    assert "fillColor=#FF0000" in style_patches[cid]        # FALSE → 红
    assert "&gt; 10 t" in value_patches[cid]                # 阈值符号 → 数值
