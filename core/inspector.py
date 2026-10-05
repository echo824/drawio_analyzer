"""模板检视 inspector（只读、零副作用）。

对**任意未注册**的 .drawio 文件做解析 + 结构体检，输出巡检报告：
  - 顶点/边计数、业务码清单（按 P/R/C 分组）、未识别码顶点
  - 重复业务码（V01 同源判据）、悬空边（source/target 缺失或指向不存在顶点）
  - 小写业务码提示（V02 同源判据，大小写归一由解析器完成，此处仅告警原始形态）

定位：热部署/多版本（Phase 3）的只读前身——解析与体检底座与 TemplateValidator
共享同一套模型，但**不读注册表、不写缓存、不需要配套 YAML**，够不着注册流程的
临时文件也能检视（如上线模拟版模板、业务方新供图）。

消费面：
  - 库：  from core.inspector import inspect_file
  - CLI： python -m core.inspector <file.drawio> [--json]
  - HTTP：POST /api/v1/templates/inspect {"path": "...", "pattern": "(可选)"}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .drawio_parser import DrawioParseError, DrawioParser
from .models import TemplateModel

DEFAULT_CODE_PATTERN = r"^\s*([PRC])(\d+(?:-\d+)?)\b"

# V02 同源：原始文本以小写 p/r/c 起头（归一后可解析，但提示图方统一大写）
_LOWERCASE_CODE = re.compile(r"^[prc]\d+(?:-\d+)?\b")


@dataclass(slots=True)
class InspectReport:
    """单次检视结论（可 JSON 序列化）。"""

    path: str
    ok: bool                                   # 解析成功且无重复码
    node_count: int = 0
    edge_count: int = 0
    business_count: int = 0
    codes_by_kind: dict[str, list[str]] = field(default_factory=dict)
    unclassified: list[str] = field(default_factory=list)   # 未识别出业务码的顶点 label 摘要
    duplicate_codes: list[str] = field(default_factory=list)
    dangling_edges: list[str] = field(default_factory=list)
    lowercase_codes: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "ok": self.ok,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "business_count": self.business_count,
            "codes_by_kind": self.codes_by_kind,
            "unclassified": self.unclassified,
            "duplicate_codes": self.duplicate_codes,
            "dangling_edges": self.dangling_edges,
            "lowercase_codes": self.lowercase_codes,
            "parse_error": self.parse_error,
        }

    def summary(self) -> str:
        if not self.ok and self.parse_error:
            return f"✗ 解析失败: {self.path}\n  {self.parse_error}"
        lines = [
            f"✓ 解析成功: {self.path}",
            f"  顶点 {self.node_count} / 连线 {self.edge_count} / 业务节点 {self.business_count}",
        ]
        for kind in sorted(self.codes_by_kind):
            codes = self.codes_by_kind[kind]
            lines.append(f"  {kind} × {len(codes)}: {', '.join(codes)}")
        if self.duplicate_codes:
            lines.append(f"  ⚠ 重复业务码: {', '.join(self.duplicate_codes)}")
        if self.dangling_edges:
            lines.append(f"  ⚠ 悬空边 {len(self.dangling_edges)} 条: {', '.join(self.dangling_edges)}")
        if self.lowercase_codes:
            lines.append(f"  ⚠ 小写码(已归一): {', '.join(self.lowercase_codes)}")
        if self.unclassified:
            lines.append(f"  ⚠ 未识别码顶点 {len(self.unclassified)} 个: {'; '.join(self.unclassified)}")
        return "\n".join(lines)


def _analyze(model: TemplateModel) -> InspectReport:
    codes_by_kind: dict[str, list[str]] = {}
    lowercase: list[str] = []
    for n in model.business_nodes():
        assert n.code is not None
        codes_by_kind.setdefault(n.code[0], []).append(n.code)
        if _LOWERCASE_CODE.match(n.label.strip()):
            lowercase.append(n.code)
    ids = {n.cell_id for n in model.nodes}
    dangling = [
        e.cell_id for e in model.edges
        if not e.source or not e.target or e.source not in ids or e.target not in ids
    ]
    unclassified = [
        (n.label.strip()[:40] or f"(空文本 {n.cell_id})")
        for n in model.unclassified_nodes()
        if n.label.strip()  # 纯空顶点（容器/占位）不列
    ]
    dup = sorted(model.duplicate_codes)
    return InspectReport(
        path="",  # 由调用方填充
        ok=not dup,
        node_count=len(model.nodes),
        edge_count=len(model.edges),
        business_count=len(model.business_nodes()),
        codes_by_kind={k: sorted(v) for k, v in codes_by_kind.items()},
        unclassified=sorted(unclassified),
        duplicate_codes=dup,
        dangling_edges=sorted(dangling),
        lowercase_codes=sorted(lowercase),
    )


def inspect_file(path: str | Path, code_pattern: str = DEFAULT_CODE_PATTERN) -> InspectReport:
    """解析并体检任意明文 .drawio；解析失败返回 ok=False + parse_error，不抛异常。"""
    p = Path(path)
    try:
        model = DrawioParser(code_pattern).parse(p)
    except DrawioParseError as exc:
        return InspectReport(path=str(p), ok=False, parse_error=str(exc))
    report = _analyze(model)
    report.path = str(p)
    return report


# ── CLI ──────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.inspector",
                                 description="检视任意 .drawio（只读，无需注册模板）")
    ap.add_argument("file", help=".drawio 文件路径")
    ap.add_argument("--pattern", default=DEFAULT_CODE_PATTERN, help="业务码提取正则")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)
    # Windows GBK 控制台无法编码 ✓/⚠ 等字符：输出面强制 UTF-8（库层不受影响）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    report = inspect_file(args.file, args.pattern)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
          if args.json else report.summary())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
