"""渲染回写（需求 §6）。ISSUE-1.6 落地结果着色（fillColor）；§6.6 改版见 1.7，HTML 组装见 1.8。

ISSUE-1.6 职责（§6.1–6.3）：
  - 仅替换"参与求值节点"的 fillColor（绿/红/黄，取自 style.yaml result_style 字符串键）；
  - 其余 style 键（strokeColor/shape/html/fontSize/aspect…）与顺序原样保留；
  - 未求值节点 / 连线 / UNCLASSIFIED 一律透传（不进补丁）；
  - 全程只作用于内存模型或输出串，绝不改动原始 .drawio。

§6.6 改版（2026-10 右值全面阈值化）：
  - 描述文本中的阈值占位符（x13 / x_p005_region…）→ 具体数值（上线时经 thresholds 传入）；
  - 括号（…）/(…) 内为解释性文字，绝不处理（如 P013 的"（井距<350米）"）；
  - 旧"实测值插到运算符前"的回写方式已整体退役。
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


# ── §6.6 改版：阈值占位符 → 具体数值（2026-10 右值全面阈值化）────────
# 左边界拦字母/数字/下划线（防 "ax1"、防符号名后半段误命中）；右边界仅拦数字/下划线：
# 既防 "x1" 误伤 "x13"/"x10" 与 "x_p005_region"，又允许单位字母紧随（"x4m"→"5m"）、
# 中文不属于 ASCII 集，"x3个"→"3个" 同样生效。
# 多符号单趟交替替换、长符号优先（x_p005_region 先于 x_p005），杜绝级联替换。
_EDGE_LEFT = r"(?<![A-Za-z0-9_])"
_EDGE_RIGHT = r"(?![0-9_])"

# 括号区间：全角/半角各自配对，括号内文字一律不替换。
# 非嵌套配对（贪婪排除同类括号）可容忍模板里的混用笔误，如 P001 "（(砂岩-有效)/3+有效）"。
_BRACKET_RES = (re.compile(r"（[^（）]*）"), re.compile(r"\([^()]*\)"))


def _display_number(value: float | int) -> str:
    """number_format: raw —— 按传入原样，整数值浮点去小数点（12.0→12，12.3→12.3）。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _format_value(value: Any) -> str:
    return _display_number(value) if isinstance(value, (int, float)) else str(value)


def compile_symbol_pattern(values: dict[str, Any]) -> re.Pattern[str] | None:
    """为"已取值(非 None)"的阈值符号建单趟交替正则；无可替换符号返回 None。"""
    names = sorted((s for s, v in values.items() if v is not None), key=len, reverse=True)
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(f"{_EDGE_LEFT}(?:{alt}){_EDGE_RIGHT}")


def _apply_substitution(text: str, pattern: re.Pattern[str], values: dict[str, Any]) -> str:
    """掩蔽括号区间后，对其余片段做单趟符号→数值替换；嵌套/重叠区间只掩一次。"""
    sub = lambda m: _format_value(values[m.group(0)])  # noqa: E731
    spans = sorted(
        (s for rx in _BRACKET_RES for s in rx.finditer(text)), key=lambda m: (m.start(), -m.end())
    )
    parts: list[str] = []
    pos = 0
    for span in spans:
        if span.start() < pos:
            continue                                   # 被外层区间覆盖
        parts.append(pattern.sub(sub, text[pos:span.start()]))
        parts.append(text[span.start():span.end()])     # 括号内原样
        pos = span.end()
    parts.append(pattern.sub(sub, text[pos:]))
    return "".join(parts)


def substitute_label(label: str, values: dict[str, Any]) -> str:
    """纯函数入口：单条 label 的阈值占位符替换（批量场景请复用 Renderer.substitute）。"""
    pattern = compile_symbol_pattern(values)
    if pattern is None:
        return label
    return _apply_substitution(label, pattern, values)


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
        # §6.6 改版：阈值占位符替换配置（style.yaml threshold_substitution 段）
        self.substitution = self.style.get("threshold_substitution", {}) or {}

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

    # ── §6.6 改版：阈值占位符 → 具体数值（仅描述含符号的节点；括号内不处理）──
    def substitute(self, model: TemplateModel, symbols: dict[str, Any]) -> dict[str, str]:
        """生成 {cell_id: 新 label} 补丁：把描述中的阈值符号换成具体数值。

        - scope=predicate_only 时仅处理 P 节点（R/C 透传）；
        - 符号未取值(None)或不在符号表 → 原样保留；后期模板直接写数字时自然无匹配；
        - 替换后 label 不再含符号 → 天然幂等，重复渲染不叠加。"""
        if not self.substitution.get("enabled", False) or not symbols:
            return {}
        values = {k: v for k, v in symbols.items() if v is not None}
        pattern = compile_symbol_pattern(values)
        if pattern is None:
            return {}
        predicate_only = self.substitution.get("scope", "predicate_only") == "predicate_only"
        patches: dict[str, str] = {}
        for node in model.nodes:
            if node.code is None:
                continue                                   # 非业务节点透传
            if predicate_only and node.kind != "P":
                continue                                   # R/C 不改描述
            new_label = _apply_substitution(node.label, pattern, values)
            if new_label != node.label:
                patches[node.cell_id] = new_label
        return patches

    @staticmethod
    def apply_attr_to_xml(root: ET.Element, patches: dict[str, str], attr: str = "value") -> int:
        changed = 0
        for cell in root.iter("mxCell"):
            cid = cell.get("id")
            if cid in patches:
                cell.set(attr, patches[cid])
                changed += 1
        return changed

    def substitute_drawio(
        self, path: str | Path, model: TemplateModel, symbols: dict[str, Any]
    ) -> str:
        """只读解析原始 .drawio → 应用阈值替换补丁 → 返回新 XML 串（原文件不变）。"""
        patches = self.substitute(model, symbols)
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
        symbols: dict[str, Any] | None = None,
    ) -> str:
        """只读解析原始 .drawio → 叠加 style(1.6)+阈值替换(§6.6改版) 补丁 → 序列化整图 XML。
        原始文件不变；输出保留 mxfile/diagram/mxGraphModel 层级以保布局与连线。"""
        root = SafeET.parse(str(path)).getroot()
        self.apply_to_xml(root, self.colorize(model, result))
        if symbols:
            self.apply_attr_to_xml(root, self.substitute(model, symbols), attr="value")
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
        symbols: dict[str, Any] | None = None,
        title: str = "评价结果",
    ) -> dict[str, Any]:
        """汇总输出：整图 XML + 内嵌 viewer 的 HTML + 结构化 JSON（供 1.9 内容协商）。"""
        diagram_xml = self.to_diagram_xml(path, model, result, symbols=symbols)
        summary = self.summary_dict(model, result)
        html_out = self.to_html(diagram_xml, title=title, summary=summary)
        return {"html": html_out, "xml": diagram_xml, "summary": summary}

    def render_svg(self, *args: Any, **kwargs: Any) -> str:
        # TODO(Phase 2): SVG 矢量输出预留（§6.5）；主输出为 HTML。
        raise NotImplementedError("SVG 输出预留于 Phase 2（主输出为 HTML）")


__all__ = [
    "Renderer",
    "apply_fill_color",
    "substitute_label",
    "compile_symbol_pattern",
    "VIEWER_SRC",
]
