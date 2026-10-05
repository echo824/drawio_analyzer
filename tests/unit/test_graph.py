"""共享图算法单测（core.graph：拓扑排序 + 环检测，需求 §4.5 / V12）。"""
from __future__ import annotations

from core.graph import find_cycle, toposort


# ── 拓扑排序：children 先于 parent ──────────────────────
def test_toposort_children_before_parents() -> None:
    children_map = {
        "C001": ["R01", "R02"],
        "R01": ["P001", "P002"],
        "R02": ["P003"],
        "P001": [],
        "P002": [],
        "P003": [],
    }
    order, cycle = toposort(children_map)
    assert cycle is None
    assert set(order) == set(children_map)          # 全节点入序
    pos = {code: i for i, code in enumerate(order)}
    assert pos["P001"] < pos["R01"] < pos["C001"]   # 逐级 child 在前
    assert pos["P003"] < pos["R02"] < pos["C001"]


def test_toposort_diamond_is_stable() -> None:
    children_map = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    order, cycle = toposort(children_map)
    assert cycle is None
    pos = {code: i for i, code in enumerate(order)}
    assert pos["D"] < pos["B"] < pos["A"]
    assert pos["D"] < pos["C"] < pos["A"]


# ── 环检测 ─────────────────────────────────────────────
def test_find_cycle_none_on_dag() -> None:
    assert find_cycle({"A": ["B"], "B": ["C"], "C": []}) is None


def test_find_cycle_returns_closed_path() -> None:
    cycle = find_cycle({"A": ["B"], "B": ["A"]})
    assert cycle is not None
    assert cycle[0] == cycle[-1]                    # 首尾回到同一点
    assert set(cycle) == {"A", "B"}


def test_find_cycle_self_loop() -> None:
    assert find_cycle({"A": ["A"]}) == ["A", "A"]


def test_external_edges_ignored_in_cycle_check() -> None:
    # 引用了不在图内的码 X/Y：拓扑与环检测只统计图内边，不因此判环
    order, cycle = toposort({"A": ["X"], "B": ["Y", "A"]})
    assert cycle is None
    assert set(order) == {"A", "B"}


def test_find_cycle_matches_toposort_second_slot() -> None:
    cyclic = {"A": ["B"], "B": ["C"], "C": ["A"]}
    _, cycle = toposort(cyclic)
    assert find_cycle(cyclic) == cycle              # find_cycle 是 toposort 的环投影
