"""模板脚手架生成器（开发态 CLI 工具，2026-10-06）。

作用：从任意带业务码的 .drawio 生成一套**结构可加载**的五件套草稿，
人工只需确认机器推不出的语义，而不必从零抄格式。

自动化边界（与"新模板无法全自动注册"的拍板同一套事实）：
- ✅ 业务码清单与 P/R/C 分组、R/C 的 children（按图连线拓扑推导）
- ✅ 聚合算子按业务新规自动定档（R=AND / C=OR）、阈值占位符号表自洽
- ✅ template/style/sample 骨架（style 缺省复制 v1）
- ❌ P 节点比较式语义：左值字段名（中文描述→英文契约）、运算符方向、
  阈值真值 → 全部落 `TODO(human)` 注释，等人确认后模板才可注册生效

定位：仅生成草稿到 <out_root>/<template_id>（目录已存在则拒绝，不覆盖）；
不触碰注册表/服务进程，人工补完按五件套流程 reload 注册。

用法：
    python -m core.scaffolder 我的新图.drawio --id my_business_v1 [--name 中文名]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .drawio_parser import DrawioParser
from .inspector import DEFAULT_CODE_PATTERN

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_STYLE_SOURCE = _REPO_ROOT / "templates" / "oil_fracturing_v1" / "style.yaml"


@dataclass(slots=True)
class ScaffoldResult:
    """生成摘要：路径、规模与人工待办清单。"""

    template_id: str
    path: Path
    p_codes: list[str] = field(default_factory=list)
    r_codes: list[str] = field(default_factory=list)
    c_codes: list[str] = field(default_factory=list)
    children_pairs: int = 0          # rules 草稿里推出的 R/C→children 引用条数
    direction: str = "forward"       # children 推导采用的连线朝向（source=父 / target=父）
    warnings: list[str] = field(default_factory=list)

    @property
    def todo_count(self) -> int:
        return len(self.p_codes) * 3   # 每个 P：字段名 + 运算符 + 阈值真值

    def summary(self) -> str:
        lines = [
            f"模板草稿已生成: {self.path}",
            f"  业务码: P×{len(self.p_codes)} R×{len(self.r_codes)} C×{len(self.c_codes)}"
            f"，连线推导 children 引用 {self.children_pairs} 条",
            "",
            "人工待办（TODO(human) 全文可搜）:",
        ]
        lines += [
            f"  1. rules.yaml：{len(self.p_codes)} 个 P 节点的比较式（左值字段名/运算符方向）",
            "  2. parameters.yaml：quantities 字段字典（单位/范围）与 thresholds 真值占位",
            f"  3. R/C children 按图连线方向自动推导（当前按 {self.direction}）——抽查 2~3 处确认没有画反",
            "  补完后：python -m core.inspector 校验图 → 重启/reload 注册模板",
        ]
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


def _clean_label(label: str, limit: int = 70) -> str:
    """节点文本压成一行注释（去标签/实体/空白，截断）。"""
    text = re.sub(r"<[^>]+>", " ", html.unescape(label or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _derive_children(
    edges: list, cell2code: dict[str, str], kind_by_code: dict[str, str]
) -> tuple[dict[str, list[str]], int, str]:
    """沿连线推导 children：两种朝向各建一遍，选"叶子 P 拥有下游"异常少的一边。

    返回 (children, 选定边的异常数, 朝向名)。图连线无统一方向约定，故自动探测。
    """
    def build(reverse: bool) -> tuple[dict[str, list[str]], int]:
        children: dict[str, list[str]] = {}
        anomalies = 0
        for e in edges:
            src, dst = cell2code.get(e.source or ""), cell2code.get(e.target or "")
            if not src or not dst:
                continue                          # 悬空边/指向非业务顶点：忽略（inspector 会报）
            if reverse:
                src, dst = dst, src
            if kind_by_code.get(src) == "P":
                anomalies += 1                    # 叶子不应有下游 → 方向可疑
            bucket = children.setdefault(src, [])
            if dst not in bucket:
                bucket.append(dst)
        return children, anomalies

    fwd, f_an = build(False)
    rev, r_an = build(True)
    if r_an < f_an:
        return rev, r_an, "target=父（反向）"
    return fwd, f_an, "source=父（正向）"


def scaffold(
    drawio_path: str | Path,
    template_id: str,
    out_root: str | Path = "templates",
    name: str | None = None,
    style_source: str | Path | None = None,
) -> ScaffoldResult:
    """解析 .drawio → 生成五件套草稿；目录已存在抛 FileExistsError。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", template_id):
        raise ValueError(f"非法模板 id: {template_id!r}（仅允许字母/数字/-/_）")
    out_dir = Path(out_root) / template_id
    if out_dir.exists():
        raise FileExistsError(f"目标目录已存在，拒绝覆盖: {out_dir}")

    model = DrawioParser(DEFAULT_CODE_PATTERN).parse(Path(drawio_path))
    cell2code = {cid: code for code, cid in model.code_map.items()}
    label_by_code = {n.code: n.label for n in model.nodes if n.code}
    kind_by_code = {n.code: n.kind for n in model.nodes if n.code}

    # children：自动探测连线朝向后推导
    children, anomalies, direction = _derive_children(model.edges, cell2code, kind_by_code)
    warnings: list[str] = []
    if anomalies:
        warnings.append(
            f"连线方向按 {direction} 推导仍有 {anomalies} 条异常边（P 作为起点）：请逐条核对连线")

    codes = {k: sorted(c for c, kk in kind_by_code.items() if kk == k) for k in ("P", "R", "C")}
    result = ScaffoldResult(
        template_id=template_id, path=out_dir,
        p_codes=codes["P"], r_codes=codes["R"], c_codes=codes["C"],
        children_pairs=sum(len(v) for k, v in children.items() if k[0] in "RC"),
        direction=direction,
        warnings=warnings,
    )

    out_dir.mkdir(parents=True)
    shutil.copy2(drawio_path, out_dir / "flow.drawio")
    (out_dir / "rules.yaml").write_text(_rules_yaml(result, children, label_by_code), encoding="utf-8")
    (out_dir / "parameters.yaml").write_text(_parameters_yaml(result, label_by_code), encoding="utf-8")
    (out_dir / "template.yaml").write_text(_template_yaml(result, name), encoding="utf-8")
    src_style = Path(style_source) if style_source else _DEFAULT_STYLE_SOURCE
    if src_style.is_file():
        shutil.copy2(src_style, out_dir / "style.yaml")
    else:
        (out_dir / "style.yaml").write_text(
            "# TODO(human): style.yaml 源缺失，请从既有模板复制\nresult_style: {}\n", encoding="utf-8")
        warnings.append("style.yaml 未复制成功（源不存在），需人工补")
    (out_dir / "sample_request.json").write_text(
        json.dumps(_sample_json(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _rules_yaml(r: ScaffoldResult, children: dict[str, list[str]], labels: dict[str, str]) -> str:
    out = [
        "# rules.yaml —— 脚手架草稿（未定稿），搜索 TODO(human) 逐处确认后方可注册",
        "meta:",
        f"  template: {r.template_id}",
        '  rules_version: "2026.10"      # TODO(human): 上线首版定版；后续结构变更必 bump（YYYY.MM）',
        "  default_aggregate: AND",
        "  evaluation_order: bottom_up",
        "",
        "# ── P 判据（比较式语义机器不可推：字段名/运算符/阈值全部待人工确认）──",
    ]
    for code in r.p_codes:
        out += [
            f"{code}:",
            "  type: predicate",
            "  logic: AND               # TODO(human): 多操作数时的节点内合并（原文或→OR）",
            "  operands:",
            f"    - {{ name: TODO_{code.lower()}_field, op: \"<\", threshold: x_{code.lower()} }}",
            f"  # 图原文: {_clean_label(labels.get(code, ''))}",
        ]
    out += ["", "# ── R 关系（算子按新规 R=AND；children 按图连线推导，请抽查方向）──"]
    for code in r.r_codes:
        out += [f"{code}:", "  type: rule", "  aggregate: AND",
                f"  children: {children.get(code, [])}  # TODO(human): 核对方向/完整性"]
    out += ["", "# ── C 结论（算子按新规 C=OR）──"]
    for code in r.c_codes:
        out += [f"{code}:", "  type: conclusion", "  aggregate: OR",
                f"  children: {children.get(code, [])}  # TODO(human): 核对方向/完整性"]
    return "\n".join(out) + "\n"


def _parameters_yaml(r: ScaffoldResult, labels: dict[str, str]) -> str:
    out = [
        "# parameters.yaml —— 脚手架草稿：quantities 与 P 判据字段一一对应，阈值与 rules 符号自洽",
        "quantities:",
    ]
    for code in r.p_codes:
        out += [
            f"  TODO_{code.lower()}_field:                # 图原文: {_clean_label(labels.get(code, ''))}",
            '    kind: measurement                        # 外部直传，服务不派生',
            '    unit: "TODO(human)"',
            "    range: [0, 100]                          # TODO(human): 合理取值区间",
        ]
    out += ["", "thresholds:  # 占位 0=非法上线值，正是提醒替换用的"]
    for code in r.p_codes:
        out.append(f"  x_{code.lower()}: {{ value: 0, unit: \"TODO(human)\" }}   # {code} 右值")
    return "\n".join(out) + "\n"


def _template_yaml(r: ScaffoldResult, name: str | None) -> str:
    return f"""# template.yaml —— 脚手架草稿
template:
  id: {r.template_id}
  name: {name or r.template_id}
  version: 1.0.0
  status: draft                   # 人工补完确认后再改 active

files:
  flow: flow.drawio
  rules: rules.yaml
  parameters: parameters.yaml
  style: style.yaml

node_code:
  pattern: '{DEFAULT_CODE_PATTERN}'
  case_insensitive: true
  anchor: business_code
"""


def _sample_json(r: ScaffoldResult) -> dict:
    return {
        "template": r.template_id,
        "well_id": "W-SAMPLE",
        "_comment": "脚手架草稿：node_values 键= P 业务码，值待人工按比较式字段填入（null=缺失→UNKNOWN）",
        "node_values": {code: None for code in r.p_codes},
    }


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows GBK 控制台
    ap = argparse.ArgumentParser(prog="core.scaffolder",
                                 description="从 .drawio 生成模板五件套草稿（人工补完 TODO 后注册）")
    ap.add_argument("drawio", help="业务码 .drawio 文件路径")
    ap.add_argument("--id", required=True, help="新模板 id（= 目录名，字母数字/-/_）")
    ap.add_argument("--name", help="模板中文名（写 template.yaml）")
    ap.add_argument("--out-root", default="templates", help="输出根目录（默认 templates/）")
    ap.add_argument("--style-source", help="style.yaml 复制源（默认库内 v1）")
    args = ap.parse_args(argv)
    try:
        result = scaffold(args.drawio, args.id, args.out_root, args.name, args.style_source)
    except (FileExistsError, ValueError, FileNotFoundError) as exc:
        print(f"✗ 生成失败: {exc}")
        return 1
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
