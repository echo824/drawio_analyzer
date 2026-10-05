"""算式符号抽取单测（core.expr.extract_expr_symbols，供校验 V23/V24 复用）。"""
from __future__ import annotations

import pytest

from core.expr import extract_expr_symbols


@pytest.mark.parametrize(
    ("expr", "plain", "refs"),
    [
        # rules.yaml P005：@基准 - 常数阈值
        ("@avg_water_cut - x_p005_y", {"x_p005_y"}, {"avg_water_cut"}),
        # rules.yaml P007：系数 * @基准
        ("x9 * @avg_liquid", {"x9"}, {"avg_liquid"}),
        # rules.yaml P003：@基准 * 系数
        (
            "@block_avg_oil_daily * x_neighbor_factor",
            {"x_neighbor_factor"},
            {"block_avg_oil_daily"},
        ),
        # 纯基准引用
        ("@block_avg_pressure", set(), {"block_avg_pressure"}),
        # 多个基准 + 多个裸符号混合、含括号与一元负号
        ("(@avg_oil * x11) - @avg_liquid / x8", {"x11", "x8"}, {"avg_oil", "avg_liquid"}),
        # 纯数字：无标识符
        ("10 + 2 * 3", set(), set()),
        # 重复符号去重
        ("@avg_liquid + x9 - x9", {"x9"}, {"avg_liquid"}),
        # @ 后允许空白
        ("@ avg_oil * x11", {"x11"}, {"avg_oil"}),
    ],
)
def test_extract_expr_symbols(expr: str, plain: set[str], refs: set[str]) -> None:
    got_plain, got_refs = extract_expr_symbols(expr)
    assert got_plain == plain
    assert got_refs == refs


def test_empty_expr_returns_empty_sets() -> None:
    assert extract_expr_symbols("") == (set(), set())
