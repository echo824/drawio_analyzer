"""渲染回写（需求 §6）。ISSUE-1.6 落地结果着色；§6.6 改版见 1.7，HTML 组装见 1.8。

ISSUE-1.6 职责（§6.1–6.3）：
  - 仅对"参与求值节点"施加结果样式，按 style.yaml result_style.channel 分发（2026-10-08 双通道）：
      fill（旧、默认、向后兼容）：替换 fillColor 为绿/红/黄；
      stroke（现行 v1）：换 strokeColor + fontStyle 加粗，**背景色不动**（模板底色自有业务含义）；
  - 其余 style 键（shape/html/fontSize/aspect…）与顺序原样保留；
  - 未求值节点 / 连线 / UNCLASSIFIED 一律透传（不进补丁）；
  - 全程只作用于内存模型或输出串，绝不改动原始 .drawio。

§6.6 改版（2026-10 右值全面阈值化）：
  - 描述文本中的阈值占位符（x13 / x_p005_region…）→ 具体数值（上线时经 thresholds 传入）；
  - 括号（…）/(…) 内为解释性文字，绝不处理（如 P013 的"（井距<350米）"）。

§4.6 参数值回写标注（2026-10 恢复，与 §6.6 替换共存）：
  - 把传入的实测值以全角（值）插到该量对应比较运算符之前（如 P009 “本井日产油（2） < 3 t”）；
  - 运算符锚点只认实体与中文词，绝不认裸 < / >（防误伤 <span> 等标签）；仅输入 P 节点回写、R/C 透传。
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


def _set_style_key(style: str, key: str, value: str) -> str:
    """§6.3 样式合并：按 ';' 切分，仅设置指定键，保留其它键及其顺序；缺失则追加。"""
    parts = [p for p in style.split(";") if p != ""]
    out: list[str] = []
    replaced = False
    for part in parts:
        k = part.partition("=")[0].strip()
        if k == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(part)
    if not replaced:
        out.append(f"{key}={value}")
    return ";".join(out) + ";"


def apply_fill_color(style: str, fill: str) -> str:
    """旧 fill 通道：仅替换 fillColor（保留向后兼容）。"""
    return _set_style_key(style, "fillColor", fill)


def apply_stroke_color(style: str, color: str) -> str:
    """2026-10-08 stroke 通道：仅替换 strokeColor（边线色），背景色保留模板原语义。"""
    return _set_style_key(style, "strokeColor", color)


def apply_bold(style: str) -> str:
    """fontStyle 按位或 1（加粗）且保留其余位（斜体/下划线）；键缺失则置 1；幂等。"""
    parts = [p for p in style.split(";") if p != ""]
    out: list[str] = []
    replaced = False
    for part in parts:
        k, _, v = part.partition("=")
        if k.strip() == "fontStyle":
            try:
                bits = int(v or 0)
            except ValueError:
                bits = 0
            out.append(f"fontStyle={bits | 1}")
            replaced = True
        else:
            out.append(part)
    if not replaced:
        out.append("fontStyle=1")
    return ";".join(out) + ";"


def _style_get(style: str, key: str) -> str | None:
    """读 style 串中某键的原始值（不存在返回 None），仅用于幂等判定。"""
    for part in style.split(";"):
        k, _, v = part.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def apply_stroke_width(style: str, width: int) -> str:
    """设置 strokeWidth（边线粗细，draw.io 缺省 1）；非整数/<1 兜底 1，同值不改写（幂等）。

    模板节点普遍不书写 strokeWidth（走默认细线），故补上该键即可产生可见变化。"""
    try:
        w = int(width)
    except (TypeError, ValueError):
        w = 1
    w = max(1, w)
    if _style_get(style, "strokeWidth") == str(w):
        return style
    return _set_style_key(style, "strokeWidth", str(w))


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


# ── §4.6 参数值回写标注（与阈值替换共存：把实测值以（值）插到运算符前）──
# 运算符定位只认「实体形式」(&gt;/&lt;…) 与「中文词」(大于/小于…)，
# 绝不匹配裸 < / >（避免误伤 <div>/<span> 等标签）；最长优先防「大于」抢「大于等于」。
_OP_CANON = {
    "&gt;=": ">=", "&lt;=": "<=", "&gt;": ">", "&lt;": "<", "&ne;": "!=",
    "≥": ">=", "≤": "<=", "≠": "!=",
    "大于等于": ">=", "小于等于": "<=", "不小于": ">=", "不大于": "<=",
    "不等于": "!=", "等于": "==", "大于": ">", "小于": "<",
}
_OP_RE = re.compile("|".join(re.escape(k) for k in sorted(_OP_CANON, key=len, reverse=True)))
_SPACE = " \t\u3000"


def format_annotation_value(raw: Any, va: dict[str, Any]) -> str | None:
    """返回括号内的裸文本；None 表示不注入（缺失/UNKNOWN，且未配置占位）。"""
    if raw is None:
        return va.get("unknown", "") or None            # 默认 "" → 不显示
    if isinstance(raw, bool):                           # 先于数值判定（bool 是 int 子类）
        return va.get("bool_true", "是") if raw else va.get("bool_false", "否")
    if isinstance(raw, (int, float)):
        return _display_number(raw)
    return str(raw)


def annotate_label(label: str, bindings: list[tuple[str, str | None]], va: dict[str, Any]) -> str:
    """把各操作数的 （值） 插到其对应运算符之前（§4.6）。

    - bindings 按去重操作数声明顺序，每项 (op, display)；display=None 表示该量 UNKNOWN/缺失；
    - 逐量按序消费同运算符的**首次未用出现**：即使 display=None 也占位消费（保留槽位），
      避免前置量缺失时后置量的值错插到前置量运算符之前（位置对齐）；
    - 无匹配运算符（如散文"不严重"/casing_damage 的 ==）→ 优雅跳过该量，不追加到末尾；
    - 括号取全角（）；幂等：目标位已是该 （值） 则不重复插；只在运算符前追加、不改原字符。
    """
    open_b, close_b = (va.get("brackets") or ["（", "）"])[:2]
    matches = [(m.start(), _OP_CANON[m.group(0)]) for m in _OP_RE.finditer(label)]
    used: set[int] = set()
    inserts: list[tuple[int, str]] = []
    for op, display in bindings:
        op_norm = (op or "").strip()
        anchor = next((pos for pos, canon in matches if canon == op_norm and pos not in used), None)
        if anchor is None:
            continue                                     # 无锚点 → 优雅跳过（不占位、不追加末尾）
        used.add(anchor)                                 # 占位消费：无论是否插入都锁定该运算符
        if display is None:
            continue                                     # UNKNOWN/缺失：保留槽位但不注入
        insert_at = anchor
        while insert_at > 0 and label[insert_at - 1] in _SPACE:
            insert_at -= 1
        text = f"{open_b}{display}{close_b}"
        if label[:insert_at].rstrip().endswith(text):
            continue                                     # 幂等：已存则不重复
        inserts.append((insert_at, text))
    result = label
    for pos, text in sorted(inserts, key=lambda x: x[0], reverse=True):
        result = result[:pos] + text + result[pos:]
    return result


class Renderer:
    """依 style.yaml 对模板模型施加结果配色。"""

    def __init__(self, style: dict[str, Any]) -> None:
        self.style = style or {}
        result_style = self.style.get("result_style", {}) or {}
        self.result_style = result_style
        self.channel = result_style.get("channel", "fill")
        # stroke 通道边线粗细（2026-10-08 强化）：缺省 3（draw.io 默认 1），<=1 则不写该键
        try:
            self.stroke_width = int(result_style.get("stroke_width", 3))
        except (TypeError, ValueError):
            self.stroke_width = 3
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
        # §4.6：参数值回写标注配置（style.yaml value_annotation 段，与阈值替换共存）
        self.value_annotation = self.style.get("value_annotation", {}) or {}

    # ── 状态 → 结果色 ───────────────────────────────────
    def spec_for(self, state: TriState) -> dict | None:
        """result_style 中该状态的定义块（键为字符串 "TRUE"…）。"""
        spec = self.result_style.get(state.value)
        return spec if isinstance(spec, dict) else None

    def fill_for(self, state: TriState) -> str | None:
        """旧 fill 通道取色（保留向后兼容）。"""
        spec = self.spec_for(state)
        return spec.get("fillColor") if spec else None

    def result_color(self, state: TriState) -> str | None:
        """按通道取“结果色”：stroke → strokeColor；fill → fillColor。供摘要/快照。"""
        spec = self.spec_for(state)
        if spec is None:
            return None
        return spec.get("strokeColor" if self.channel == "stroke" else "fillColor")

    def _colors_kind(self, kind: str | None) -> bool:
        """该节点种类是否施加结果色（color_kinds 为空则全施加）。"""
        return self.color_kinds is None or (kind in self.color_kinds)

    # ── 生成 style 补丁（cell_id → 新 style）────────────
    def colorize(self, model: TemplateModel, result: EvaluationResult) -> dict[str, str]:
        """仅对"有结果样式且有求值态"且属于 color_kinds 的业务节点产出补丁；其余透传。

        channel=fill（旧）：替换 fillColor；channel=stroke（2026-10-08）：换 strokeColor+加粗+
        可选加粗边线（strokeWidth），背景色不动（模板底色自有含义）。两通道均只改内存/输出串，幂等可重复渲染。"""
        style_by_cell = {n.cell_id: n.style for n in model.nodes}
        kind_by_code = {n.code: n.kind for n in model.nodes if n.code is not None}
        patches: dict[str, str] = {}
        for code, cell_id in model.code_map.items():
            if not self._colors_kind(kind_by_code.get(code)):
                continue                             # 如 R 节点：不改样式
            state = result.node_states.get(code)
            if state is None:
                continue
            original = style_by_cell.get(cell_id)
            if original is None:
                continue
            if self.channel == "stroke":
                spec = self.spec_for(state)
                if spec is None:
                    continue
                new_style = original
                color = spec.get("strokeColor")
                if color:
                    new_style = apply_stroke_color(new_style, color)
                if spec.get("bold"):
                    new_style = apply_bold(new_style)
                if self.stroke_width > 1:            # 边线加粗（>1 才写，=1 走默认不污染）
                    new_style = apply_stroke_width(new_style, self.stroke_width)
                if new_style != original:            # 幂等：无变化不入补丁
                    patches[cell_id] = new_style
            else:
                fill = self.fill_for(state)
                if fill is None:
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

    # ── §4.6 参数值回写（仅输入 P 节点；实测值（值）插到运算符前）──
    def annotate(
        self, nodes: dict[str, Any], node_values: dict[str, Any], model: TemplateModel
    ) -> dict[str, str]:
        """对"出现在输入里的 P 节点"生成 {cell_id: 新 label}：缺失量不插、布尔→（是/否）、幂等。"""
        va = self.value_annotation
        if not va.get("enabled", False) or not nodes or not node_values:
            return {}
        label_by_cell = {n.cell_id: n.label for n in model.nodes}
        kind_by_cell = {n.cell_id: n.kind for n in model.nodes}
        patches: dict[str, str] = {}
        for code, vals in node_values.items():
            node_def = nodes.get(code)
            if node_def is None or getattr(node_def, "type", None) != "predicate":
                continue                                 # R/C 不回写
            cell_id = model.code_map.get(code)
            if not cell_id or kind_by_cell.get(cell_id) != "P":
                continue
            bindings = self._bindings(node_def, vals, va)
            base = label_by_cell.get(cell_id)
            if not bindings or base is None:
                continue
            new_label = annotate_label(base, bindings, va)
            if new_label != base:
                patches[cell_id] = new_label
        return patches

    @staticmethod
    def _bindings(node: Any, vals: Any, va: dict[str, Any]) -> list[tuple[str, str | None]]:
        """按去重后的操作数顺序组装 (op, display)；每个量各占一位（display 可为 None）。

        None 值（UNKNOWN/缺失）仍保留其运算符槽位，避免后置量的值错插到前置量运算符之前。"""
        if not isinstance(vals, dict):
            return []
        seen: set[str] = set()
        bindings: list[tuple[str, str | None]] = []
        for operand in node.operands or []:
            name = operand.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            bindings.append((operand.get("op", ""), format_annotation_value(vals.get(name), va)))
        return bindings

    def value_patches(
        self,
        model: TemplateModel,
        symbols: dict[str, Any] | None = None,
        nodes: dict[str, Any] | None = None,
        node_values: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """§6.6 阈值替换 与 §4.6 值回写 的合成补丁（先换符号、再插实测值）。"""
        symbols = symbols or {}
        node_values = node_values or {}
        values = {k: v for k, v in symbols.items() if v is not None}
        pattern = (
            compile_symbol_pattern(values)
            if (self.substitution.get("enabled", False) and values)
            else None
        )
        predicate_only = self.substitution.get("scope", "predicate_only") == "predicate_only"
        ann_on = self.value_annotation.get("enabled", False) and bool(nodes) and bool(node_values)
        patches: dict[str, str] = {}
        for node in model.nodes:
            if node.code is None:
                continue
            label = node.label
            if pattern is not None and (node.kind == "P" or not predicate_only):
                label = _apply_substitution(label, pattern, values)
            if ann_on and node.kind == "P":
                node_def = nodes.get(node.code)
                if node_def is not None and getattr(node_def, "type", None) == "predicate":
                    bindings = self._bindings(node_def, node_values.get(node.code), self.value_annotation)
                    if bindings:
                        label = annotate_label(label, bindings, self.value_annotation)
            if label != node.label:
                patches[node.cell_id] = label
        return patches

    # ── ISSUE-1.8：整图 XML / HTML / 结构化 JSON 输出 ──────────
    def to_diagram_xml(
        self,
        path: str | Path,
        model: TemplateModel,
        result: EvaluationResult,
        *,
        symbols: dict[str, Any] | None = None,
        nodes: dict[str, Any] | None = None,
        node_values: dict[str, Any] | None = None,
    ) -> str:
        """只读解析原始 .drawio → 叠加 style(1.6)+阈值替换(§6.6)+值回写(§4.6) 补丁 → 序列化整图 XML。
        原始文件不变；输出保留 mxfile/diagram/mxGraphModel 层级以保布局与连线。"""
        root = SafeET.parse(str(path)).getroot()
        self.apply_to_xml(root, self.colorize(model, result))
        patches = self.value_patches(model, symbols, nodes, node_values)
        if patches:
            self.apply_attr_to_xml(root, patches, attr="value")
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
                colors[code] = self.result_color(state)
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
        nodes: dict[str, Any] | None = None,
        node_values: dict[str, Any] | None = None,
        title: str = "评价结果",
    ) -> dict[str, Any]:
        """汇总输出：整图 XML + 内嵌 viewer 的 HTML + 结构化 JSON（供 1.9 内容协商）。"""
        diagram_xml = self.to_diagram_xml(
            path, model, result, symbols=symbols, nodes=nodes, node_values=node_values
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
    "substitute_label",
    "compile_symbol_pattern",
    "annotate_label",
    "format_annotation_value",
    "VIEWER_SRC",
]
