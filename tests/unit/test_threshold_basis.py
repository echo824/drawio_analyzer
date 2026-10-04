"""ISSUE-1.2 阈值与基准解析单测（需求 §5.1/§5.3/§11.4）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import load_templates
from core.expr import ExprError, build_parameter_context

TEMPLATE_ID = "oil_fracturing_v1"


@pytest.fixture()
def cfg(templates_root: Path):  # noqa: ANN201
    return load_templates(templates_root)[TEMPLATE_ID]


@pytest.fixture()
def sample_request(templates_root: Path) -> dict:  # noqa: ANN201
    path = templates_root / TEMPLATE_ID / "sample_request.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── 默认阈值快照 + 请求覆盖 ──────────────────────────────
def test_default_symbols_from_parameters(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    assert ctx.symbol("x_p005_y") == 8      # B8：从 rules.yaml 外提的 Y 常数
    assert ctx.symbol("x13") == 70
    assert ctx.symbol("x3") == 3
    assert ctx.symbol("x_neighbor_factor") == 1.5
    assert ctx.symbol("no_such_symbol") is None


def test_request_threshold_overrides_default(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x13": 60}})
    assert ctx.symbol("x13") == 60          # 覆盖生效
    # 解耦：默认表未被污染（不改 cfg.parameters）
    assert cfg.thresholds["x13"]["value"] == 70
    assert build_parameter_context(cfg).symbol("x13") == 70


def test_request_threshold_can_add_new_symbol(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x_custom": 42}})
    assert ctx.symbol("x_custom") == 42


# ── 外部基准（@引用）─────────────────────────────────────
def test_basis_ref_and_missing(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"basis": {"avg_water_cut": 55}})
    assert ctx.ref("avg_water_cut") == 55
    assert ctx.ref("block_avg_pressure") is None   # 未提供 → None（不报错）


# ── 表达式安全求值 ───────────────────────────────────────
def test_eval_expr_sub_symbol_and_ref(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"basis": {"avg_water_cut": 55}})
    # @avg_water_cut - x_p005_y  => 55 - 8
    assert ctx.eval_expr("@avg_water_cut - x_p005_y") == 47


def test_eval_expr_multiplication_precedence(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"basis": {"avg_liquid": 25}})
    # x9 * @avg_liquid  => 0.8 * 25
    assert ctx.eval_expr("x9 * @avg_liquid") == pytest.approx(20.0)


def test_eval_expr_missing_returns_none(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)          # 无任何 basis
    assert ctx.eval_expr("@block_avg_pressure") is None
    assert ctx.eval_expr("@avg_oil - x10") is None   # 一侧缺失即 None


def test_eval_expr_rejects_unsupported_element(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    with pytest.raises(ExprError):
        ctx.eval_expr("__import__('os')")        # 函数调用不被允许


# ── resolve_rhs：三类右值形态 ────────────────────────────
def test_resolve_rhs_threshold_expr_value(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(
        cfg, {"basis": {"block_avg_oil_daily": 6}}
    )
    assert ctx.resolve_rhs({"threshold": "x3"}) == 3
    # @block_avg_oil_daily * x_neighbor_factor => 6 * 1.5
    assert ctx.resolve_rhs({"expr": "@block_avg_oil_daily * x_neighbor_factor"}) == 9.0
    assert ctx.resolve_rhs({"value": False}) is False   # 布尔字面量原样返回
    assert ctx.resolve_rhs({}) is None                  # 未知形态 → None


# ── 黄金样例：sample_request.json 全量基准解析 ───────────
def test_sample_request_full_context(cfg, sample_request: dict) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, sample_request)
    # P005 第二判据：@avg_water_cut - x_p005_y => 55 - 8
    assert ctx.eval_expr("@avg_water_cut - x_p005_y") == 47
    # P003：@block_avg_oil_daily * x_neighbor_factor => 6 * 1.5
    assert ctx.eval_expr("@block_avg_oil_daily * x_neighbor_factor") == 9.0
    # P007：x9 * @avg_liquid => 0.8 * 25
    assert ctx.eval_expr("x9 * @avg_liquid") == pytest.approx(20.0)
    # P008：x7 * @avg_liquid => 0.5 * 25
    assert ctx.eval_expr("x7 * @avg_liquid") == pytest.approx(12.5)
    # P009：x11 * @avg_oil => 0.5 * 6
    assert ctx.eval_expr("x11 * @avg_oil") == pytest.approx(3.0)
    # 请求覆盖 x13=70 与默认一致；x12 覆盖 10
    assert ctx.symbol("x12") == 10


# ── 求值边界 ────────────────────────────────────────────
def test_eval_expr_supports_unary_minus(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    assert ctx.eval_expr("-x3 + x1") == 2     # -3 + 5


def test_eval_expr_string_literal_raises(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    with pytest.raises(ExprError):
        ctx.eval_expr('"abc"')                # 非数值字面量不支持


def test_resolve_token_prefers_symbol_then_basis(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"basis": {"avg_oil": 6}})
    assert ctx.resolve_token("x3") == 3          # 常数优先
    assert ctx.resolve_token("avg_oil") == 6      # 裸名回退到基准
    assert ctx.resolve_token("nope") is None


# ── 外部传入值的规范化 ────────────────────────────────
def test_string_number_is_coerced(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x13": "60"}})
    assert ctx.symbol("x13") == 60.0             # 字符串数值自动转型


def test_non_numeric_value_becomes_none(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"basis": {"avg_oil": "abc"}})
    assert ctx.ref("avg_oil") is None             # 无法解析→None（上层判 UNKNOWN）
