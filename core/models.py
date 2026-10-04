"""共享运行时类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TriState(str, Enum):
    """三态求值结果（需求 §4.1）。"""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class EvalNode:
    code: str
    kind: str                 # P | R | C | UNCLASSIFIED
    state: TriState
    label: str | None = None


@dataclass(slots=True)
class EvaluationResult:
    """规则引擎输出，供渲染器消费（需求 §8.2 渲染解耦）。"""

    template_id: str
    well_id: str | None
    rules_version: str | None
    node_states: dict[str, TriState] = field(default_factory=dict)
    root_state: TriState | None = None


@dataclass(slots=True)
class Node:
    """Draw.io 顶点（需求 §3）。kind: P|R|C|UNCLASSIFIED。"""

    cell_id: str
    kind: str
    code: str | None          # 业务码（= 节点 ID）；UNCLASSIFIED 为 None
    label: str
    style: str
    geometry: tuple[int, int, int, int]  # (x, y, width, height)
    parent: str | None = None


@dataclass(slots=True)
class Edge:
    """Draw.io 连线。缺失 source/target 即悬空边（由 Phase 2 校验 V10 拦截）。"""

    cell_id: str
    source: str | None
    target: str | None
    style: str


@dataclass(slots=True)
class TemplateModel:
    """解析后的内部模板模型（运行时数据，需求 §10.3）。"""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    code_map: dict[str, str] = field(default_factory=dict)  # 业务码 -> 当前 mxCell.id
    duplicate_codes: set[str] = field(default_factory=set)  # 供 V01 校验

    def business_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.code is not None]

    def unclassified_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.code is None]

    def dangling_edges(self) -> list[Edge]:
        return [e for e in self.edges if not e.source or not e.target]
