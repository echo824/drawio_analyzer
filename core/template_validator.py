"""模板一致性校验 V01-V31（需求 §7）。Phase 2 落地。

对解析后的模板模型 + rules + parameters 做错误级/告警级校验；
失败则模板不入缓存、保留上一份有效模板、返回明确错误。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Level(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(slots=True)
class Finding:
    code: str          # V01..V31
    level: Level
    message: str
    node: str | None = None


@dataclass(slots=True)
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.level is Level.ERROR for f in self.findings)


class TemplateValidator:
    def validate(self, model: dict[str, Any], cfg: Any) -> ValidationReport:
        # TODO(Phase 2/ISSUE-2.x): 落地 V01-V31
        raise NotImplementedError
