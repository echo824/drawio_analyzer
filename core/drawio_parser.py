"""Draw.io XML 解析（需求 §3、§10.3）。ISSUE-1.1 落地。

职责：
  - 用 defusedxml 安全解析 flow.drawio（禁 DTD/外部实体，V30）
  - 提取节点 id/type(text)/position/size/style、edge(source/target/style)
  - 从 value 前缀用 node_code_pattern 提取并归一业务码，构建 code_map（业务码→当前 mxCell.id）
  - 建立内部 TemplateModel（运行时数据，不落地；原始文件只读）
"""
from __future__ import annotations

import re
from pathlib import Path

import defusedxml
import defusedxml.ElementTree as SafeET  # 安全 XML 解析（防 XXE/实体膨胀）

from .models import Edge, Node, TemplateModel

# 业务码首字母 → kind（大写后）
_KIND_BY_PREFIX = {"P": "P", "R": "R", "C": "C"}


class DrawioParseError(Exception):
    """解析失败（压缩/非明文、缺少模型、被安全策略拒绝等）。"""


def _compile(pattern: str) -> re.Pattern[str]:
    # 大小写归一：p003 → P003（需求 §3.4）
    return re.compile(pattern, re.IGNORECASE)


def extract_business_code(value: str | None, pattern: re.Pattern[str]) -> str | None:
    """从节点 value 文本前缀提取业务码；失败返回 None。

    返回大写、去除首尾空白的业务码（如 'P003'、'R02-1'）。
    """
    if not value:
        return None
    match = pattern.search(value.strip())
    return match.group(0).upper() if match else None


def parse_style(style: str) -> dict[str, str]:
    """把 draw.io 的 `key=value;key=value;` 样式串解析为有序 dict（键值均去空）。"""
    result: dict[str, str] = {}
    for part in style.split(";"):
        if not part:
            continue
        key, sep, val = part.partition("=")
        result[key] = val if sep else ""
    return result


class DrawioParser:
    """将 .drawio 解析为内部模板模型 TemplateModel。"""

    def __init__(self, node_code_pattern: str) -> None:
        self.pattern = _compile(node_code_pattern)

    # ── 对外入口 ──────────────────────────────────────────
    def parse(self, path: str | Path) -> TemplateModel:
        root = self._safe_parse(path)
        models = root.findall(".//mxGraphModel")
        if not models:
            self._raise_for_compression(root)
        model = TemplateModel()
        for graph_model in models:
            self._collect(graph_model, model)
        return model

    # ── 内部 ──────────────────────────────────────────────
    @staticmethod
    def _safe_parse(path: str | Path):
        try:
            return SafeET.parse(str(path)).getroot()
        except defusedxml.DefusedXmlException as exc:  # 禁用 DTD/实体/外部引用（V30）
            raise DrawioParseError(f"XML 安全校验失败（疑似 XXE/实体膨胀）: {exc}") from exc
        except SafeET.ParseError as exc:  # 非法 XML
            raise DrawioParseError(f"XML 解析失败: {exc}") from exc

    @staticmethod
    def _raise_for_compression(root) -> None:
        # 明文要求（V31）：diagram 内应含 mxGraphModel；若仅有文本则视为压缩。
        for diagram in root.findall(".//diagram"):
            if (diagram.text or "").strip():
                raise DrawioParseError(
                    "检测到压缩/非明文 .drawio（V31）：请在 draw.io 中取消压缩后另存为明文。"
                )
        raise DrawioParseError("未在 .drawio 中找到 mxGraphModel。")

    def _collect(self, graph_model, model: TemplateModel) -> None:
        for cell in graph_model.iter("mxCell"):
            cell_id = cell.get("id")
            if cell_id is None:
                continue
            if cell.get("edge") == "1":
                model.edges.append(
                    Edge(
                        cell_id=cell_id,
                        source=cell.get("source"),
                        target=cell.get("target"),
                        style=cell.get("style", "") or "",
                    )
                )
            elif cell.get("vertex") == "1":
                model.nodes.append(self._make_node(cell, cell_id, model))

    def _make_node(self, cell, cell_id: str, model: TemplateModel) -> Node:
        raw_value = cell.get("value", "") or ""
        code = extract_business_code(raw_value, self.pattern)
        kind = _KIND_BY_PREFIX.get(code[0]) if code else None
        if code and kind is None:  # 理论上不会发生（正则限定 P/R/C），兜底
            kind = "UNCLASSIFIED"
        kind = kind or "UNCLASSIFIED"

        if code:
            if code in model.code_map:
                model.duplicate_codes.add(code)  # 供 V01 检出同码多节点
            else:
                model.code_map[code] = cell_id

        return Node(
            cell_id=cell_id,
            kind=kind,
            code=code,
            label=raw_value,
            style=cell.get("style", "") or "",
            geometry=self._geometry(cell),
            parent=cell.get("parent"),
        )

    @staticmethod
    def _geometry(cell) -> tuple[int, int, int, int]:
        geo = cell.find("mxGeometry")
        if geo is None:
            return (0, 0, 0, 0)
        return (
            _to_int(geo.get("x")),
            _to_int(geo.get("y")),
            _to_int(geo.get("width")),
            _to_int(geo.get("height")),
        )


def _to_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


__all__ = [
    "DrawioParser",
    "DrawioParseError",
    "extract_business_code",
    "parse_style",
]
