"""三态规则引擎（需求 §4）。ISSUE-1.4 落地叶层（P 节点）；R/C 聚合见 ISSUE-1.5。

叶层职责：
  - 对每个 P，按 operands 逐项比较：左值取自"校验后的 node_values"（缺失=哨兵 None），
    右值用 ParameterContext(ISSUE-1.2) 解析 threshold/expr/value；
  - 任一操作数缺左值或缺右值 → 该子条件 = UNKNOWN；
  - 节点内多操作数按 logic(默认 AND / OR) 用 Kleene 强三值真值表合并，能短路优先短路（§4.4）。
"""
from __future__ import annotations

import operator
from typing import Any

from .expr import ParameterContext
from .graph import toposort
from .models import EvaluationResult, TriState

# 支持的比较运算符（模板作者笔误之外的全部形态）
_CMP = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def compare(left: Any, op: str, right: Any) -> TriState:
    """单个比较 → TRUE/FALSE；运算符非法抛错；类型不可比降级为 UNKNOWN。"""
    fn = _CMP.get((op or "").strip())
    if fn is None:
        raise ValueError(f"不支持的比较运算符: {op!r}")
    try:
        return TriState.TRUE if fn(left, right) else TriState.FALSE
    except TypeError:
        return TriState.UNKNOWN


def merge(states: list[TriState], logic: str = "AND") -> TriState:
    """Kleene AND/OR 合并（§4.4）。空列表→UNKNOWN；其它逻辑（WEIGHTED/VOTE）留待上层。"""
    logic = (logic or "AND").strip().upper()
    if not states:
        return TriState.UNKNOWN
    if logic == "OR":
        if TriState.TRUE in states:
            return TriState.TRUE          # 短路
        return TriState.UNKNOWN if TriState.UNKNOWN in states else TriState.FALSE
    if logic == "AND":
        if TriState.FALSE in states:
            return TriState.FALSE         # 短路
        return TriState.UNKNOWN if TriState.UNKNOWN in states else TriState.TRUE
    raise ValueError(f"节点内不支持的逻辑: {logic!r}")


class RuleEngine:
    """基于 rules.yaml 节点定义 + 数值上下文求值。"""

    def __init__(self, nodes: dict[str, Any], context: ParameterContext) -> None:
        self.nodes = nodes
        self.context = context

    @classmethod
    def from_config(cls, cfg: Any, context: ParameterContext) -> RuleEngine:
        return cls(cfg.nodes, context)

    # ── ISSUE-1.4：P 叶节点求值 ──────────────────────────
    def evaluate_predicate(self, code: str, node_values: dict[str, Any]) -> TriState:
        node = self.nodes.get(code)
        if node is None or getattr(node, "type", None) != "predicate":
            raise ValueError(f"节点 {code!r} 不是 predicate(P)，无法按叶层求值")
        states = [
            self._eval_operand(code, operand, node_values)
            for operand in (node.operands or [])
        ]
        return merge(states, node.logic)

    def evaluate_all_predicates(self, node_values: dict[str, Any]) -> dict[str, TriState]:
        """对所有 P 节点求值，返回 {业务码: TriState}。"""
        return {
            code: self.evaluate_predicate(code, node_values)
            for code, node in self.nodes.items()
            if getattr(node, "type", None) == "predicate"
        }

    # ── 内部：单操作数 ──────────────────────────────────
    def _eval_operand(self, code: str, operand: dict[str, Any], node_values: dict[str, Any]) -> TriState:
        name = operand.get("name")
        left = (node_values.get(code) or {}).get(name)
        if left is None:                       # 缺失/非法（校验器哨兵）→ UNKNOWN
            return TriState.UNKNOWN
        right = self.context.resolve_rhs(operand)
        if right is None:                      # 阈值/基准缺失 → UNKNOWN
            return TriState.UNKNOWN
        return compare(left, operand.get("op", ""), right)

    # ── ISSUE-1.5：R/C 聚合 + 拓扑排序 + 结果组装 ────────
    def evaluate_states(self, node_values: dict[str, Any]) -> dict[str, TriState]:
        """自底向上（P → R → C）按拓扑序求值全部节点，返回 {业务码: TriState}。"""
        order = self._toposort()            # 子节点先于父节点；检出环
        states: dict[str, TriState] = {}
        for code in order:
            node = self.nodes[code]
            ntype = getattr(node, "type", None)
            if ntype == "predicate":
                states[code] = self.evaluate_predicate(code, node_values)
            elif ntype in ("rule", "conclusion"):
                child_states = [states.get(ch, TriState.UNKNOWN) for ch in (node.children or [])]
                states[code] = self._aggregate(node, child_states)
            else:  # 不应发生（loader 保证 type）
                states[code] = TriState.UNKNOWN
        return states

    def evaluate(
        self,
        node_values: dict[str, Any],
        *,
        template_id: str,
        well_id: str | None = None,
        rules_version: str | None = None,
    ) -> EvaluationResult:
        """全链路求值，组装 EvaluationResult（含根结论 C001）。"""
        states = self.evaluate_states(node_values)
        root_code = self._root_code()
        return EvaluationResult(
            template_id=template_id,
            well_id=well_id,
            rules_version=rules_version,
            node_states=states,
            root_state=states.get(root_code) if root_code else None,
        )

    def _root_code(self) -> str | None:
        for code, node in self.nodes.items():
            if getattr(node, "is_root", False):
                return code
        return None

    def _toposort(self) -> list[str]:
        """自底向上拓扑序（children 先于 parent）；检出环即抛错（共享算法见 core.graph）。"""
        children_map = {
            code: (getattr(node, "children", None) or [])
            for code, node in self.nodes.items()
        }
        order, cycle = toposort(children_map)
        if cycle is not None:
            raise ValueError(f"rules.yaml 依赖存在环: {' → '.join(cycle)}")
        return order

    def _aggregate(self, node: Any, child_states: list[TriState]) -> TriState:
        agg = (getattr(node, "aggregate", None) or "AND").strip().upper()
        if agg in ("AND", "OR"):
            return merge(child_states, agg)
        if agg == "VOTE":
            return aggregate_vote(child_states)
        if agg == "WEIGHTED":
            quota = getattr(node, "quota", None)
            if quota is None:
                raise ValueError("WEIGHTED 聚合需在 rules.yaml 指定 quota")
            return aggregate_weighted(child_states, self._weights(node), float(quota))
        raise ValueError(f"未知聚合算子: {agg!r}")

    @staticmethod
    def _weights(node: Any) -> list[float]:
        """权重视 children 顺序：支持 dict(子码→权) / list(对齐) / 默认均 1。"""
        children = getattr(node, "children", None) or []
        w = getattr(node, "weights", None)
        if isinstance(w, dict):
            return [float(w.get(ch, 1.0)) for ch in children]
        if isinstance(w, (list, tuple)) and len(w) == len(children):
            return [float(x) for x in w]
        return [1.0] * len(children)


def aggregate_vote(states: list[TriState]) -> TriState:
    """多数表决（§4.4）：UNKNOWN 不计入分母；已确定中 TRUE 多数→TRUE，
    FALSE 多数→FALSE，平票或无已确定→UNKNOWN。"""
    true_n = states.count(TriState.TRUE)
    false_n = states.count(TriState.FALSE)
    if true_n + false_n == 0:
        return TriState.UNKNOWN
    if true_n > false_n:
        return TriState.TRUE
    if false_n > true_n:
        return TriState.FALSE
    return TriState.UNKNOWN


def aggregate_weighted(states: list[TriState], weights: list[float], quota: float) -> TriState:
    """加权和≥ quota 则 TRUE；即使所有 UNKNOWN 转 TRUE 仍不足则 FALSE；否则 UNKNOWN。
    （quota 为绝对权重阈值；当前模板未使用，为可配置骨架。）"""
    if not states or sum(weights) <= 0:
        return TriState.UNKNOWN
    true_w = sum(w for s, w in zip(states, weights) if s == TriState.TRUE)
    unk_w = sum(w for s, w in zip(states, weights) if s == TriState.UNKNOWN)
    if true_w >= quota:
        return TriState.TRUE
    if true_w + unk_w < quota:
        return TriState.FALSE
    return TriState.UNKNOWN


__all__ = [
    "RuleEngine",
    "compare",
    "merge",
    "aggregate_vote",
    "aggregate_weighted",
]
