"""ISSUE-1.2 阈值解析单测（需求 §5.1/§11.4；右值全面阈值化后无 expr/basis 运算）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import load_templates
from core.expr import ParameterContext, build_parameter_context

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
    assert ctx.symbol("x13") == 70
    assert ctx.symbol("x3") == 3
    assert ctx.symbol("casing_min") == 105
    # 右值表达式塌缩后的阈值占位符（上线时经 thresholds 覆盖为真实数值）
    assert ctx.symbol("x_p003") == 9
    assert ctx.symbol("x_p005_region") == 47
    assert ctx.symbol("x_p012_block") == 600
    assert ctx.symbol("no_such_symbol") is None


def test_removed_coefficient_symbols_absent(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    # expr 时代的系数/Y 常数已随右值阈值化退役
    for gone in ("x7", "x9", "x11", "x_p005_y", "x_neighbor_factor", "x_well_distance"):
        assert gone not in cfg.thresholds
        assert ctx.symbol(gone) is None


def test_request_threshold_overrides_default(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x13": 60}})
    assert ctx.symbol("x13") == 60          # 覆盖生效
    # 解耦：默认表未被污染（不改 cfg.parameters）
    assert cfg.thresholds["x13"]["value"] == 70
    assert build_parameter_context(cfg).symbol("x13") == 70


def test_request_threshold_can_add_new_symbol(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x_custom": 42}})
    assert ctx.symbol("x_custom") == 42


# ── resolve_rhs：两类右值形态（threshold / value）────────
def test_resolve_rhs_threshold_and_value(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    assert ctx.resolve_rhs({"threshold": "x3"}) == 3
    assert ctx.resolve_rhs({"threshold": "x_p005_region"}) == 47
    assert ctx.resolve_rhs({"value": False}) is False   # 布尔字面量原样返回
    assert ctx.resolve_rhs({}) is None                  # 未知形态 → None
    # expr 形态已退役：即便残留也解析为 None（判 UNKNOWN，不再求值）
    assert ctx.resolve_rhs({"expr": "@avg_water_cut - 8"}) is None


def test_resolve_rhs_missing_threshold_is_none(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg)
    assert ctx.resolve_rhs({"threshold": "x_not_defined"}) is None


# ── 黄金样例：sample_request.json 全量阈值解析 ───────────
def test_sample_request_full_context(cfg, sample_request: dict) -> None:  # noqa: ANN001
    assert "basis" not in sample_request                 # 契约已去 basis 化
    ctx = build_parameter_context(cfg, sample_request)
    assert ctx.symbol("x13") == 70                       # 请求覆盖与默认一致
    assert ctx.symbol("x12") == 10
    assert ctx.symbol("x_p008_region") == 12.5           # 沿用占位默认
    assert ctx.resolve_rhs({"threshold": "x_p003"}) == 9


# ── 外部传入值的规范化 ────────────────────────────────
def test_string_number_is_coerced(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x13": "60"}})
    assert ctx.symbol("x13") == 60.0             # 字符串数值自动转型


def test_non_numeric_value_becomes_none(cfg) -> None:  # noqa: ANN001
    ctx = build_parameter_context(cfg, {"thresholds": {"x_p003": "abc"}})
    assert ctx.symbol("x_p003") is None          # 无法解析→None（上层判 UNKNOWN）


def test_explicit_context_smoke() -> None:
    ctx = ParameterContext(symbols={"x13": 70})
    assert ctx.symbol("x13") == 70
    assert ctx.resolve_rhs({"threshold": "x13"}) == 70
