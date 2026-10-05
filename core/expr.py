"""阈值与基准解析（需求 §5.1/§5.3/§11.4）。ISSUE-1.2 落地。

职责（不涉及三态判定，仅提供"数值上下文"给 ISSUE-1.4/1.5）：
  - 合并默认阈值(parameters.yaml.thresholds) + 请求覆盖(request.thresholds)；
  - 载入外部基准(request.basis) 作为 @引用 的取值来源；
  - 安全求值 rules.yaml 中的 expr（如 `@avg_water_cut - x_p005_y`、`x9 * @avg_liquid`），
    仅支持 + - * / 与一元 +/-，禁止函数/下标/属性访问（防注入）；
  - 任一引用的符号/基准缺失 → 返回 None（不报错，交由求值层判 UNKNOWN）。
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from typing import Any

Number = float | int

# 仅允许这些算术运算，避免任意代码执行
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# @name —— 基准引用；解析前替换为合法标识符占位，记录哪些是 ref
_AT_REF = re.compile(r"@\s*([A-Za-z_]\w*)")
_REF_PREFIX = "_atref_"
# 标识符（裸符号 / @引用改写后均以字母/下划线开头）；数字字面量不会被匹配
_IDENT = re.compile(r"[A-Za-z_]\w*")


class ExprError(Exception):
    """表达式非法（语法/不受支持的元素）。区别于"值缺失"（后者返回 None）。"""


@dataclass(slots=True)
class ParameterContext:
    """求值用的数值上下文：常数阈值 symbols + 外部基准 basis。"""

    symbols: dict[str, Number | None] = field(default_factory=dict)
    basis: dict[str, Number | None] = field(default_factory=dict)

    # ── 单点取值 ──────────────────────────────────────────
    def symbol(self, name: str) -> Number | None:
        """常数阈值（默认 + 覆盖）。"""
        return self.symbols.get(name)

    def ref(self, name: str) -> Number | None:
        """外部基准 @引用（request.basis）。缺失返回 None。"""
        return self.basis.get(name)

    def resolve_token(self, token: str) -> Number | None:
        """裸符号解析：先常数，再基准。找不到返回 None。"""
        if token in self.symbols:
            return self.symbols[token]
        return self.basis.get(token)

    # ── 表达式求值 ────────────────────────────────────────
    def eval_expr(self, expr: str) -> Number | None:
        """安全求值算式；引用缺失 → None；语法/元素非法 → ExprError。"""
        rewritten, refs = _rewrite_refs(expr)
        try:
            tree = ast.parse(rewritten, mode="eval")
        except SyntaxError as exc:  # pragma: no cover - 交由上层聚合报错
            raise ExprError(f"非法表达式: {expr!r}") from exc
        return self._eval(tree.body, refs)

    def _eval(self, node: ast.AST, refs: set[str]) -> Number | None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExprError(f"不支持的字面量: {node.value!r}")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in refs:  # 来自 @引用
                return self.ref(node.id[len(_REF_PREFIX):])
            return self.resolve_token(node.id)
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ExprError(f"不支持的运算: {type(node.op).__name__}")
            left = self._eval(node.left, refs)
            right = self._eval(node.right, refs)
            return None if left is None or right is None else op(left, right)
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ExprError(f"不支持的一元运算: {type(node.op).__name__}")
            val = self._eval(node.operand, refs)
            return None if val is None else op(val)
        raise ExprError(f"表达式含不受支持的元素: {ast.dump(node)}")

    # ── 操作数右值（供 1.4 比较使用）──────────────────────
    def resolve_rhs(self, operand: dict[str, Any]) -> Number | bool | None:
        """把一个 predicate operand 的右值解析为具体数值：
        expr → 算式求值；threshold → 常数符号；value → 字面量（如布尔）。
        无法解析（缺失/未知键）返回 None。
        """
        if "expr" in operand:
            return self.eval_expr(operand["expr"])
        if "threshold" in operand:
            return self.resolve_token(operand["threshold"])
        if "value" in operand:
            return operand["value"]
        return None


def _rewrite_refs(expr: str) -> tuple[str, set[str]]:
    """把 `@name` 替换为合法标识符 `_atref_name`，并记录这些是 ref。"""
    refs: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        refs.add(_REF_PREFIX + name)
        return _REF_PREFIX + name

    return _AT_REF.sub(repl, expr), refs


def extract_expr_symbols(expr: str) -> tuple[set[str], set[str]]:
    """从算式中静态抽取符号（不求值），供校验 V23/V24 与求值层共用。

    返回 (plain, refs)：
      - plain：裸符号（约定为常数阈值/符号名，如 `x_p005_y`、`x9`）；
      - refs ：`@` 引用的基准名（不含 `@`，如 `avg_water_cut`）。
    仅做词法抽取，复用 `_rewrite_refs`/`_AT_REF`，与 `eval_expr` 的引用约定保持一致。
    """
    rewritten, _ = _rewrite_refs(expr)
    plain: set[str] = set()
    refs: set[str] = set()
    for match in _IDENT.finditer(rewritten):
        token = match.group(0)
        if token.startswith(_REF_PREFIX):
            refs.add(token[len(_REF_PREFIX):])
        else:
            plain.add(token)
    return plain, refs


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
    - basis   ← request.basis（外部基准），缺失即为 None。
    """
    request = request or {}

    symbols: dict[str, Number | None] = {}
    for key, spec in (cfg.thresholds or {}).items():
        symbols[key] = _num(spec.get("value")) if isinstance(spec, dict) else _num(spec)

    for key, val in (request.get("thresholds") or {}).items():  # 请求覆盖（解耦：不改默认表）
        symbols[key] = _num(val)

    basis: dict[str, Number | None] = {}
    for key, val in (request.get("basis") or {}).items():
        basis[key] = _num(val)

    return ParameterContext(symbols=symbols, basis=basis)


__all__ = [
    "ParameterContext",
    "ExprError",
    "build_parameter_context",
    "extract_expr_symbols",
]
