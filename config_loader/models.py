"""模板配置的结构性模型（Phase 0 基本结构断言用）。

这里只做"结构是否存在、字段是否齐全"的轻量校验；完整的规则/拓扑/参数一致性
校验（需求 V01-V31）在 Phase 2 的 core.template_validator 中实现。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TemplateMeta(BaseModel):
    """template.yaml -> template 段。"""

    model_config = ConfigDict(extra="allow")
    id: str
    name: str | None = None
    version: str | None = None
    status: str | None = None


class TemplateFiles(BaseModel):
    """template.yaml -> files 段（5 个组成文件的相对路径）。"""

    model_config = ConfigDict(extra="allow")
    flow: str
    rules: str
    parameters: str
    style: str


class TemplateDescriptor(BaseModel):
    """整份 template.yaml。"""

    model_config = ConfigDict(extra="allow")
    template: TemplateMeta
    files: TemplateFiles


class PredicateOperand(BaseModel):
    """P 节点的一个操作数：name + op + (threshold | value)。右值全面阈值化，已无 expr 形态。"""

    model_config = ConfigDict(extra="allow")
    name: str
    op: str = Field(min_length=1)


class NodeDef(BaseModel):
    """rules.yaml 中一个业务码节点的公共字段。"""

    model_config = ConfigDict(extra="allow")
    type: Literal["predicate", "rule", "conclusion"]
    aggregate: str | None = None
    logic: str | None = None
    children: list[str] | None = None
    operands: list[dict[str, Any]] | None = None
    is_root: bool = False


class RulesMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    template: str
    rules_version: str | None = None
    default_aggregate: str = "AND"
    evaluation_order: str | None = None


class StyleResult(BaseModel):
    """style.yaml -> result_style：三态必须给出 fillColor。"""

    model_config = ConfigDict(extra="allow")
    channel: str = "fill"
    TRUE: dict[str, Any]
    FALSE: dict[str, Any]
    UNKNOWN: dict[str, Any]


# rules.yaml 顶层除 meta 外都应能被解析为 NodeDef；quantities/thresholds 允许额外键
NodeMap = dict[str, NodeDef]
