"""模板一致性校验 V01-V31（需求 §7）。Phase 2 落地。

对解析后的模板模型 + rules + parameters 做错误级/告警级校验；
任一错误级不通过 → 模板不入缓存、保留上一份有效模板、返回明确错误（§7.5）。

本模块**一次收集全部 Finding**（不 fail-fast），便于坏模板一次性报出所有问题。
当前覆盖：V01/V02（业务码）、V10/V11/V12/V13（拓扑连线）、V20/V21/V22/V23（一致性）、
V30/V31（安全）；V24（基准声明表）已退役——R2 右值全面阈值化后 basis 契约整体退出，
其原始动机消失，阈值符号引用校验由 V23 覆盖（待确认项 G1，2026-10-05 拍板）。

**入缓严重度策略（待确认项 G3）**：规则求解只依赖 `cfg.nodes` 的 children 依赖图，
`.drawio` 连线仅服务渲染；故纯图结构缺陷（V01/V10/V11/V13）降级为告警、不阻断入缓，
触及规则/参数一致性的（V12/V20/V21/V22/V23）保持 ERROR 阻断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .graph import find_cycle

if TYPE_CHECKING:  # 仅类型注解，避免运行期耦合
    from .models import TemplateModel


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

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.ERROR]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level is Level.WARNING]


# V02：严格大写业务码（大小写归一由解析器负责，此处识别原始小写 → 告警）
_STRICT_CODE = re.compile(r"^[PRC]\d+(?:-\d+)?\b")
_LOOSE_CODE = re.compile(r"^[pPrRcC]\d+(?:-\d+)?\b")

# V30/V31 安全：对原始 flow.drawio 文本的正则扫描（defusedxml 已在解析期拦截实体展开，
# 此处作为显式校验码补捕：V30 裸 DOCTYPE/ENTITY→ERROR；V31 压缩 diagram→WARNING）。
_FLOW_FILENAME = "flow.drawio"
_XXE_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_COMPRESSED_RE = re.compile(r"<diagram\b[^>]*>\s*[A-Za-z0-9+/=]{16,}\s*</diagram>")

# 纯图结构类缺陷：只影响 drawio 渲染，不参与规则求解（求解依赖 cfg.nodes.children）。
# 按入缓策略（待确认项 G3）降级为非阻断告警。
_NONBLOCKING_GRAPH_CODES = frozenset({"V01", "V10", "V11", "V13"})


def _graph_finding(code: str, message: str, node: str | None = None) -> Finding:
    """纯图结构类检出的构造器：命中容错集则降为告警并附说明（待确认项 G3）。"""
    if code in _NONBLOCKING_GRAPH_CODES:
        return Finding(code, Level.WARNING, f"{message}（纯图结构缺陷，仅影响渲染，不阻断入缓）", node=node)
    return Finding(code, Level.ERROR, message, node=node)


@dataclass(slots=True)
class _Ctx:
    """预计算的共享集合，避免各规则重复遍历。"""

    model: TemplateModel
    cfg: Any
    node_ids: set[str]                 # 全部顶点 mxCell.id
    graph_codes: set[str]              # 图中解析出的业务码
    rule_codes: set[str]               # rules.yaml 声明的业务码
    thresholds: set[str]               # parameters.yaml 阈值符号
    connected: set[str]                # 被任一边连接的顶点 id
    source: str | None = None          # 原始 flow.drawio 文本（供 V30/V31；无文件则 None）


class TemplateValidator:
    """校验解析后的模板模型与配置一致性（需求 §7 V01-V31）。"""

    def validate(self, model: TemplateModel, cfg: Any) -> ValidationReport:
        ctx = self._build_ctx(model, cfg)
        report = ValidationReport()
        checks = (
            self._check_v01, self._check_v02,
            self._check_v10, self._check_v11, self._check_v12, self._check_v13,
            self._check_v20, self._check_v21, self._check_v22, self._check_v23,
            self._check_v30, self._check_v31,
        )
        for check in checks:
            report.findings.extend(check(ctx))
        return report

    # ── 上下文构建 ────────────────────────────────────────
    @staticmethod
    def _build_ctx(model: TemplateModel, cfg: Any) -> _Ctx:
        node_ids = {n.cell_id for n in model.nodes}
        connected: set[str] = set()
        for edge in model.edges:
            if edge.source:
                connected.add(edge.source)
            if edge.target:
                connected.add(edge.target)
        return _Ctx(
            model=model,
            cfg=cfg,
            node_ids=node_ids,
            graph_codes=set(model.code_map),
            rule_codes=set(cfg.nodes),
            thresholds=set(cfg.thresholds),
            connected=connected,
            source=TemplateValidator._read_flow_source(cfg),
        )

    @staticmethod
    def _read_flow_source(cfg: Any) -> str | None:
        """读原始 flow.drawio 文本供 V30/V31 扫描；cfg 无 path（合成测试）或读失败 → None。"""
        base = getattr(cfg, "path", None)
        if base is None:
            return None
        try:
            return (Path(base) / _FLOW_FILENAME).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # ── 业务码类 ──────────────────────────────────────────
    @staticmethod
    def _check_v01(ctx: _Ctx) -> list[Finding]:
        """V01 业务码唯一（错误）。"""
        return [
            _graph_finding("V01", f"业务码 {code} 重复：同码对应多个节点", node=code)
            for code in sorted(ctx.model.duplicate_codes)
        ]

    @staticmethod
    def _check_v02(ctx: _Ctx) -> list[Finding]:
        """V02 业务码前缀可解析、大小写归一（告警：原始为小写）。"""
        findings: list[Finding] = []
        for node in ctx.model.nodes:
            if node.code is None:
                continue
            label = node.label.strip()
            if _STRICT_CODE.match(label) or not _LOOSE_CODE.match(label):
                continue  # 已规范，或本就非业务码开头（交由其它规则）
            findings.append(
                Finding("V02", Level.WARNING, f"业务码 {node.code} 原始书写为小写，已归一", node=node.code)
            )
        return findings

    # ── 拓扑 / 连线类 ─────────────────────────────────────
    @staticmethod
    def _check_v10(ctx: _Ctx) -> list[Finding]:
        """V10 连线必须同时有 source 与 target（错误）。"""
        return [
            _graph_finding("V10", f"连线 {edge.cell_id} 缺少 {'source' if not edge.source else 'target'}",
                           node=edge.cell_id)
            for edge in ctx.model.dangling_edges()
        ]

    @staticmethod
    def _check_v11(ctx: _Ctx) -> list[Finding]:
        """V11 source/target 指向存在的节点（错误）。"""
        findings: list[Finding] = []
        for edge in ctx.model.edges:
            for role in ("source", "target"):
                ref = getattr(edge, role)
                if ref and ref not in ctx.node_ids:
                    findings.append(
                        _graph_finding("V11", f"连线 {edge.cell_id} 的 {role}={ref} 指向不存在的节点",
                                       node=edge.cell_id)
                    )
        return findings

    @staticmethod
    def _check_v12(ctx: _Ctx) -> list[Finding]:
        """V12 规则依赖图无环（错误）。"""
        children_map = {code: (getattr(node, "children", None) or []) for code, node in ctx.cfg.nodes.items()}
        cycle = find_cycle(children_map)
        if cycle is None:
            return []
        return [Finding("V12", Level.ERROR, f"规则依赖存在环: {' → '.join(cycle)}", node=cycle[0])]

    @staticmethod
    def _check_v13(ctx: _Ctx) -> list[Finding]:
        """V13 参与计算的节点至少被连接一次（孤立节点告警）。"""
        return [
            Finding("V13", Level.WARNING, f"节点 {node.code} 未被任何连线连接（孤立）", node=node.code)
            for node in ctx.model.nodes
            if node.code is not None and node.cell_id not in ctx.connected
        ]

    # ── 规则 / 参数一致性类 ───────────────────────────────
    @staticmethod
    def _check_v20(ctx: _Ctx) -> list[Finding]:
        """V20 图中每个业务码在 rules.yaml 有对应规则（错误）。"""
        return [
            Finding("V20", Level.ERROR, f"图中业务码 {code} 在 rules.yaml 无对应规则", node=code)
            for code in sorted(ctx.graph_codes - ctx.rule_codes)
        ]

    @staticmethod
    def _check_v21(ctx: _Ctx) -> list[Finding]:
        """V21 rules.yaml 每个业务码在图中存在对应节点（错误）。"""
        return [
            Finding("V21", Level.ERROR, f"rules.yaml 业务码 {code} 在图中无对应节点", node=code)
            for code in sorted(ctx.rule_codes - ctx.graph_codes)
        ]

    @staticmethod
    def _check_v22(ctx: _Ctx) -> list[Finding]:
        """V22 每条规则 children 引用的码都存在（错误）。"""
        findings: list[Finding] = []
        for code, node in ctx.cfg.nodes.items():
            for child in (getattr(node, "children", None) or []):
                if child not in ctx.rule_codes:
                    findings.append(
                        Finding("V22", Level.ERROR, f"规则 {code} 的 children 引用不存在的码 {child}", node=code)
                    )
        return findings

    @staticmethod
    def _check_v23(ctx: _Ctx) -> list[Finding]:
        """V23 threshold 引用的阈值符号在 parameters.yaml 定义（错误）。

        右值全面阈值化后 operand 不再携带 expr，仅检查 threshold 符号。"""
        findings: list[Finding] = []
        for code, node in ctx.cfg.nodes.items():
            for operand in (getattr(node, "operands", None) or []):
                findings.extend(_check_operand_symbols(code, operand, ctx.thresholds))
        return findings

    # ── 安全类 ────────────────────────────────────────────
    @staticmethod
    def _check_v30(ctx: _Ctx) -> list[Finding]:
        """V30 XXE/DTD 安全（错误）。

        defusedxml 已在解析期硬性拦截实体展开；此处对原始文本补捕裸 DOCTYPE/ENTITY
        声明（解析器容忍但不应入缓的情形），命中即 ERROR、阻断入缓。
        """
        if ctx.source and _XXE_RE.search(ctx.source):
            return [Finding("V30", Level.ERROR, "检测到 DTD/实体声明（XXE 风险），拒绝入缓")]
        return []

    @staticmethod
    def _check_v31(ctx: _Ctx) -> list[Finding]:
        """V31 压缩/非明文 diagram（告警，不阻断——待确认项 G2）。

        仅针对“已解析出明文模型、但夹带压缩 diagram”的可恢复情形；若整份压缩
        导致无模型可解析，解析阶段已抛 DrawioParseError，由管理器按拒绝入缓处理。
        """
        if ctx.source and _COMPRESSED_RE.search(ctx.source):
            return [Finding("V31", Level.WARNING, "检测到压缩/非明文 diagram，建议在 draw.io 取消压缩后另存")]
        return []


def _check_operand_symbols(code: str, operand: dict[str, Any], thresholds: set[str]) -> list[Finding]:
    """单个 operand 的阈值符号引用检查（V23）；右值阈值化后仅 threshold 一种符号形态。"""
    symbols: set[str] = set()
    if "threshold" in operand:
        symbols.add(str(operand["threshold"]))
    return [
        Finding("V23", Level.ERROR, f"节点 {code} 引用未定义阈值 {sym}", node=code)
        for sym in sorted(symbols - thresholds)
    ]


__all__ = ["Level", "Finding", "ValidationReport", "TemplateValidator"]
