"""共享图算法：规则依赖拓扑排序 + 环检测（需求 §4.5 / 校验 V12）。

从 `RuleEngine._toposort` 抽出，供求值层（自底向上顺序）与 Phase 2 模板校验
（V12 无环检查）共用，避免两处各写一份 DFS 导致行为漂移。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def toposort(
    children_map: Mapping[str, Sequence[str]],
) -> tuple[list[str], list[str] | None]:
    """DFS 后序拓扑排序（children 先于 parent），并检出首个环。

    参数
      children_map: 业务码 -> 其 children 序列（依赖方向 parent → children）。
      仅统计 key 也在 children_map 内的边（外部/未知引用被忽略，交由一致性校验处理）。

    返回
      (order, None)          —— 无环：order 覆盖全部 key，children 恒先于 parent。
      (partial_order, cycle) —— 存在环：cycle 为形如 [a, b, ..., a] 的环路径
                                 （以回到起点的那个码为首尾）；partial_order 不保证完整。
    """
    order: list[str] = []
    state: dict[str, int] = {}          # 0=待访(WHITE) 1=在栈(GRAY) 2=完成(BLACK)
    WHITE, GRAY, BLACK = 0, 1, 2
    cycle: list[str] | None = None

    def visit(code: str, stack: list[str]) -> None:
        nonlocal cycle
        if cycle is not None:
            return
        mark = state.get(code, WHITE)
        if mark == GRAY:
            cycle = stack[stack.index(code):] + [code]
            return
        if mark == BLACK:
            return
        state[code] = GRAY
        for ch in children_map.get(code, ()):
            if ch in children_map:
                visit(ch, stack + [code])
        state[code] = BLACK
        order.append(code)

    for code in children_map:
        visit(code, [])
    return order, cycle


def find_cycle(children_map: Mapping[str, Sequence[str]]) -> list[str] | None:
    """仅做环检测（V12）：返回首个环路径 [a, b, ..., a]，无环返回 None。"""
    _, cycle = toposort(children_map)
    return cycle


__all__ = ["toposort", "find_cycle"]
