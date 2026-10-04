"""渲染回写（需求 §6）。ISSUE-1.6 落地结果着色（fillColor）；值回写见 1.7，HTML 组装见 1.8。

ISSUE-1.6 职责（§6.1–6.3）：
  - 仅替换"参与求值节点"的 fillColor（绿/红/黄，取自 style.yaml result_style 字符串键）；
  - 其余 style 键（strokeColor/shape/html/fontSize/aspect…）与顺序原样保留；
  - 未求值节点 / 连线 / UNCLASSIFIED 一律透传（不进补丁）；
  - 全程只作用于内存模型或输出串，绝不改动原始 .drawio。
"""
from __future__ import annotations

import html as _html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as SafeET  # 安全解析原始 .drawio（只读）

from .models import EvaluationResult, TemplateModel, TriState

# draw.io 官方静态 viewer（保布局/连线/字体）
VIEWER_SRC = "https://viewer.diagrams.net/js/viewer-static.min.js"


def apply_fill_color(style: str, fill: str) -> str:
    """§6.3 样式合并：按 ';' 切分，仅设置 fillColor，保留其它键及其顺序；缺失则追加。"""
    parts = [p for p in style.split(";") if p != ""]
    out: list[str] = []
    replaced = False
    for part in parts:
        key = part.partition("=")[0].strip()
        if key == "fillColor":
            out.append(f"fillColor={fill}")
            replaced = True
        else:
            out.append(part)
    if not replaced:
        out.append(f"fillColor={fill}")
    return ";".join(out) + ";"


# ── 运算符定位（§6.6）：只匹配“实体形式”的比较运算符，避免误伤 <div> 等标签 ──
_OP_CANON = {
    "&gt;=": ">=", "&lt;=": "<=", "&gt;": ">", "&lt;": "<",
    "&ne;": "!=", "≥": ">=", "≤": "<=", "≠": "!=", "＝": "==", "=": "==",
}
_OP_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(_OP_CANON, key=len, reverse=True))
)
_SPACE = " \t\u3000"


def _display_number(value: float | int) -> str:
    """number_format: raw —— 按输入原样，整数值浮点去小数点（12.0→12，12.3→12.3）。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_annotation_value(raw: Any, va: dict[str, Any]) -> str | None:
    """返回括号内的裸文本；None 表示不注入（UNKNOWN）。"""
    if raw is None:
        unknown = va.get("unknown", "")
        return unknown or None                     # 默认 "" → 不显示
    if isinstance(raw, bool):                      # 先于数值判定（bool 是 int 子类）
        return va.get("bool_true", "是") if raw else va.get("bool_false", "否")
    if isinstance(raw, (int, float)):
        return _display_number(raw)
    return str(raw)


def annotate_label(label: str, bindings: list[tuple[str, str]], va: dict[str, Any]) -> str:
    """将 （值） 插到各操作数对应运算符之前（§6.6）。

    bindings: 按 rules.yaml 操作数顺序的 (op, display) 列表。
    同一运算符按出现顺序消费；无匹配运算符则追加到末尾。
    幂等：目标位置已是该 （display） 则跳过。仅追加，不改原文字符。
    """
    open_b, close_b = (va.get("brackets") or ["（", "）"])[:2]
    matches = [(m.start(), _OP_CANON[m.group(0)]) for m in _OP_RE.finditer(label)]
    used: set[int] = set()
    inserts: list[tuple[int, str]] = []
    for op, display in bindings:
        op_norm = (op or "").strip()
        anchor_pos = None
        for pos, canon in matches:
            if canon == op_norm and pos not in used:
                anchor_pos = pos
                used.add(pos)
                break
        insert_at = len(label) if anchor_pos is None else anchor_pos
        if anchor_pos is not None:                 # 紧贴运算符（跳过其前空白）
            while insert_at > 0 and label[insert_at - 1] in _SPACE:
                insert_at -= 1
        text = f"{open_b}{display}{close_b}"
        if label[:insert_at].rstrip().endswith(text):   # 幂等
            continue
        inserts.append((insert_at, text))
    result = label
    for pos, text in sorted(inserts, key=lambda x: x[0], reverse=True):   # 从右到左插入
        result = result[:pos] + text + result[pos:]
    return result


class Renderer:
    """依 style.yaml 对模板模型施加结果配色。"""

    def __init__(self, style: dict[str, Any]) -> None:
        self.style = style or {}
        result_style = self.style.get("result_style", {}) or {}
        self.result_style = result_style
        self.channel = result_style.get("channel", "fill")
        raw_kinds = result_style.get("color_kinds")
        # 为空/缺失 → 染所有已求值节点（向后兼容）；否则仅指定种类（如 [P, C]）
        self.color_kinds: set[str] | None = (
            {str(k).upper() for k in raw_kinds} if raw_kinds else None
        )
        # template_default_fill 仅"未求值透传"参考，运行时不覆盖、不参与类型识别
        self.template_default_fill = self.style.get("template_default_fill", {}) or {}
        untouched = self.style.get("untouched_nodes", {}) or {}
        self.passthrough_types = set(untouched.get("types", []) or [])

    # ── 状态 → 结果色 ───────────────────────────────────
    def fill_for(self, state: TriState) -> str | None:
        spec = self.result_style.get(state.value)   # result_style 键为字符串 "TRUE"…
        return spec.get("fillColor") if isinstance(spec, dict) else None

    def _colors_kind(self, kind: str | None) -> bool:
        """该节点种类是否施加结果色（color_kinds 为空则全施加）。"""
        return self.color_kinds is None or (kind in self.color_kinds)

    # ── 生成 style 补丁（cell_id → 新 style）────────────
    def colorize(self, model: TemplateModel, result: EvaluationResult) -> dict[str, str]:
        """仅对"有结果色且有求值态"且属于 color_kinds 的业务节点产出补丁；其余透传。"""
        style_by_cell = {n.cell_id: n.style for n in model.nodes}
        kind_by_code = {n.code: n.kind for n in model.nodes if n.code is not None}
        patches: dict[str, str] = {}
        for code, cell_id in model.code_map.items():
            if not self._colors_kind(kind_by_code.get(code)):
                continue                             # 如 R 节点：不改样式
            state = result.node_states.get(code)
            if state is None:
                continue
            fill = self.fill_for(state)
            original = style_by_cell.get(cell_id)
            if fill is None or original is None:
                continue
            patches[cell_id] = apply_fill_color(original, fill)
        return patches

    # ── 把补丁写入内存 XML 树（供 1.8 序列化）────────────
    @staticmethod
    def apply_to_xml(root: ET.Element, patches: dict[str, str]) -> int:
        changed = 0
        for cell in root.iter("mxCell"):
            cid = cell.get("id")
            if cid in patches:
                cell.set("style", patches[cid])
                changed += 1
        return changed

    def colorize_drawio(self, path: str | Path, model: TemplateModel, result: EvaluationResult) -> str:
        """只读解析原始 .drawio → 应用 fillColor 补丁 → 返回改色后的 XML 串（原文件不变）。"""
        patches = self.colorize(model, result)
        root = SafeET.parse(str(path)).getroot()
        self.apply_to_xml(root, patches)
        return ET.tostring(root, encoding="unicode")

    # ── ISSUE-1.7：参数值回写标注 ────────────────────────
    def annotate(
        self, nodes: dict[str, Any], node_values: dict[str, Any], model: TemplateModel
    ) -> dict[str, str]:
        """仅对“出现在输入中的 P 节点”生成 {cell_id: 新 label} 补丁（scope=predicate_only）。
        UNKNOWN（值缺失）不插；布尔→（是/否）；幂等（已存则不重复）。"""
        va = self.style.get("value_annotation", {}) or {}
        if not va.get("enabled", False):
            return {}
        label_by_cell = {n.cell_id: n.label for n in model.nodes}
        patches: dict[str, str] = {}
        for code, vals in (node_values or {}).items():
            node = nodes.get(code)
            if node is None or getattr(node, "type", None) != "predicate":
                continue                                   # R/C 不回写
            cell_id = model.code_map.get(code)
            label = label_by_cell.get(cell_id) if cell_id else None
            if label is None:
                continue
            bindings = self._bindings(node, vals, va)
            if not bindings:
                continue
            new_label = annotate_label(label, bindings, va)
            if new_label != label:
                patches[cell_id] = new_label
        return patches

    @staticmethod
    def _bindings(node: Any, vals: Any, va: dict[str, Any]) -> list[tuple[str, str]]:
        """按操作数顺序组装 (op, display)；display 为 None（UNKNOWN）则跳过。"""
        operands = node.operands or []
        single = len(operands) == 1
        bindings: list[tuple[str, str]] = []
        for operand in operands:
            name = operand.get("name")
            if isinstance(vals, dict):
                raw = vals.get(name)
            else:                                          # 标量仅适配单操作数节点
                raw = vals if single else None
            disp = format_annotation_value(raw, va)
            if disp is not None:
                bindings.append((operand.get("op", ""), disp))
        return bindings

    @staticmethod
    def apply_attr_to_xml(root: ET.Element, patches: dict[str, str], attr: str = "value") -> int:
        changed = 0
        for cell in root.iter("mxCell"):
            cid = cell.get("id")
            if cid in patches:
                cell.set(attr, patches[cid])
                changed += 1
        return changed

    def annotate_drawio(
        self, path: str | Path, nodes: dict[str, Any], node_values: dict[str, Any], model: TemplateModel
    ) -> str:
        """只读解析原始 .drawio → 应用值回写补丁 → 返回新 XML 串（原文件不变）。"""
        patches = self.annotate(nodes, node_values, model)
        root = SafeET.parse(str(path)).getroot()
        self.apply_attr_to_xml(root, patches, attr="value")
        return ET.tostring(root, encoding="unicode")

    # ── ISSUE-1.8：整图 XML / HTML / 结构化 JSON 输出 ──────────
    def to_diagram_xml(
        self,
        path: str | Path,
        model: TemplateModel,
        result: EvaluationResult,
        *,
        nodes: dict[str, Any] | None = None,
        node_values: dict[str, Any] | None = None,
    ) -> str:
        """只读解析原始 .drawio → 叠加 style(1.6)+value(1.7) 补丁 → 序列化整图 XML。
        原始文件不变；输出保留 mxfile/diagram/mxGraphModel 层级以保布局与连线。"""
        root = SafeET.parse(str(path)).getroot()
        self.apply_to_xml(root, self.colorize(model, result))
        if nodes is not None and node_values is not None:
            self.apply_attr_to_xml(root, self.annotate(nodes, node_values, model), attr="value")
        return ET.tostring(root, encoding="unicode")

    def summary_dict(self, model: TemplateModel, result: EvaluationResult) -> dict[str, Any]:
        """结构化评价（需求 §9.1/§117）：每个业务码三态 + 实际施加的结果色 + 最终根状态。

        colors 仅列实际被着色的节点（与 colorize 一致）；未着色种类（如 R）不列入。"""
        kind_by_code = {n.code: n.kind for n in model.nodes if n.code is not None}
        node_states: dict[str, str] = {}
        colors: dict[str, str | None] = {}
        for code, state in result.node_states.items():
            node_states[code] = state.value
            if self._colors_kind(kind_by_code.get(code)):
                colors[code] = self.fill_for(state)
        return {
            "template_id": result.template_id,
            "well_id": result.well_id,
            "rules_version": result.rules_version,
            "root_state": result.root_state.value if result.root_state else None,
            "node_states": node_states,
            "colors": colors,
        }

    @staticmethod
    def to_html(
        diagram_xml: str,
        *,
        title: str = "评价结果",
        viewer_src: str = VIEWER_SRC,
        toolbar: str = "zoom layers tags lightbox",
        summary: dict[str, Any] | None = None,
    ) -> str:
        """内嵌 draw.io graphViewer 的独立 HTML 页（§6.5）。JSON 负载经 html 转义安全内嵌。"""
        cfg = {"highlight": "#0000ff", "nav": True, "resize": True, "toolbar": toolbar, "xml": diagram_xml}
        attr = _html.escape(json.dumps(cfg, ensure_ascii=False), quote=True)
        summary_block = ""
        if summary is not None:
            payload = _html.escape(json.dumps(summary, ensure_ascii=False, indent=2), quote=False)
            summary_block = (
                '<details><summary>结构化结果 JSON</summary>'
                f'<pre class="summary">{payload}</pre></details>'
            )
        parts = [
            "<!DOCTYPE html>",
            '<html lang="zh"><head><meta charset="utf-8">',
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            f"<title>{_html.escape(title)}</title>",
            "<style>body{font-family:'Microsoft YaHei',Arial,sans-serif;margin:0;padding:16px"
            ";background:#fafafa}.mxgraph{max-width:100%;border:1px solid #e0e0e0;background:#fff"
            ";padding:8px}pre.summary{background:#f4f4f4;padding:12px;overflow:auto;border-radius:4px}</style>",
            "</head><body>",
            f"<h1>{_html.escape(title)}</h1>",
            f'<div class="mxgraph" data-mxgraph="{attr}"></div>',
            summary_block,
            f'<script src="{_html.escape(viewer_src, quote=True)}"></script>',
            "</body></html>",
        ]
        return "\n".join(p for p in parts if p)

    def render(
        self,
        path: str | Path,
        model: TemplateModel,
        result: EvaluationResult,
        *,
        nodes: dict[str, Any] | None = None,
        node_values: dict[str, Any] | None = None,
        title: str = "评价结果",
    ) -> dict[str, Any]:
        """汇总输出：整图 XML + 内嵌 viewer 的 HTML + 结构化 JSON（供 1.9 内容协商）。"""
        diagram_xml = self.to_diagram_xml(
            path, model, result, nodes=nodes, node_values=node_values
        )
        summary = self.summary_dict(model, result)
        html_out = self.to_html(diagram_xml, title=title, summary=summary)
        return {"html": html_out, "xml": diagram_xml, "summary": summary}

    def render_svg(self, *args: Any, **kwargs: Any) -> str:
        # TODO(Phase 2): SVG 矢量输出预留（§6.5）；主输出为 HTML。
        raise NotImplementedError("SVG 输出预留于 Phase 2（主输出为 HTML）")


__all__ = [
    "Renderer",
    "apply_fill_color",
    "annotate_label",
    "format_annotation_value",
    "VIEWER_SRC",
]
