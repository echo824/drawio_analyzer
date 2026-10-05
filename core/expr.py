"""阈值解析（需求 §5.1/§11.4；2026-10 右值全面阈值化后瘦身）。

职责（不涉及三态判定，仅提供"数值上下文"给 ISSUE-1.4/1.5）：
  - 合并默认阈值(parameters.yaml.thresholds) + 请求覆盖(request.thresholds)；
  - 右值仅两种形态：threshold（阈值占位符号）/ value（字面量，如布尔）；
  - 表达式求值（@引用 / +-*/）已整体退役——比较式右值一律作为单一阈值占位符，
    其具体数值由外部算好后经 thresholds 传入（见 待确认项.md R2）。
  - 引用的阈值符号缺失 → 返回 None（不报错，交由求值层判 UNKNOWN）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Number = float | int


@dataclass(slots=True)
class ParameterContext:
    """求值用的数值上下文：阈值符号表（parameters 默认 + 请求覆盖）。"""

    symbols: dict[str, Number | None] = field(default_factory=dict)

    def symbol(self, name: str) -> Number | None:
        """阈值符号取值（默认 + 覆盖）。缺失返回 None。"""
        return self.symbols.get(name)

    def resolve_rhs(self, operand: dict[str, Any]) -> Number | bool | None:
        """把一个 predicate operand 的右值解析为具体值：
        threshold → 阈值符号取值；value → 字面量（如布尔）；其余/缺失 → None。
        """
        if "threshold" in operand:
            return self.symbols.get(str(operand["threshold"]))
        if "value" in operand:
            return operand["value"]
        return None


def _num(value: Any) -> Number | None:
    """把外部传入值规范为数值；None/空串→None；非数字原样交给求值层判缺失。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value  # 布尔字面量保留（比较用），不当作数值转换目标
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_parameter_context(cfg: Any, request: dict[str, Any] | None = None) -> ParameterContext:
    """从模板配置 + 请求构建数值上下文。

    - symbols ← parameters.yaml.thresholds 的默认 value；再被 request.thresholds 覆盖（不改 cfg）。
    """
    request = request or {}

    symbols: dict[str, Number | None] = {}
    for key, spec in (cfg.thresholds or {}).items():
        symbols[key] = _num(spec.get("value")) if isinstance(spec, dict) else _num(spec)

    for key, val in (request.get("thresholds") or {}).items():  # 请求覆盖（解耦：不改默认表）
        symbols[key] = _num(val)

    return ParameterContext(symbols=symbols)


__all__ = [
    "ParameterContext",
    "build_parameter_context",
]
