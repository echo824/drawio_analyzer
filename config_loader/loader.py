"""配置加载器：发现模板目录、安全解析 5 份 YAML、做 Phase 0 基本结构断言。

用法::

    from config_loader import load_templates
    registry = load_templates("templates")
    cfg = registry["oil_fracturing_v1"]
    cfg.rules / cfg.parameters / cfg.style ...  # 原始 dict
    cfg.nodes                                   # 解析后的业务码节点
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .exceptions import TemplateNotFoundError, TemplateStructureError
from .models import (
    NodeDef,
    RulesMeta,
    StyleResult,
    TemplateDescriptor,
)

CONFIG_FILES = ("template.yaml", "rules.yaml", "parameters.yaml", "style.yaml")
# flow.drawio 由 core.drawio_parser 在 Phase 1 解析，此处仅确认文件存在


@dataclass(slots=True)
class TemplateConfig:
    """单个模板的已加载配置与派生结构。"""

    template_id: str
    path: Path
    template: dict[str, Any]
    rules: dict[str, Any]
    parameters: dict[str, Any]
    style: dict[str, Any]
    nodes: dict[str, NodeDef] = field(default_factory=dict)
    node_code_pattern: str = r"^\s*([PRC])(\d+(?:-\d+)?)\b"

    @property
    def rules_meta(self) -> RulesMeta:
        return RulesMeta.model_validate(self.rules.get("meta", {}))

    @property
    def quantities(self) -> dict[str, Any]:
        return self.parameters.get("quantities", {})

    @property
    def thresholds(self) -> dict[str, Any]:
        return self.parameters.get("thresholds", {})


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise TemplateStructureError(path.name, [f"{path.name} 顶层应为映射(mapping)"])
    return data


def _read_yaml_if_exists(path: Path) -> dict[str, Any] | None:
    """结构校验阶段用：文件不存在返回 None（缺失已在别处记为 problem）。"""
    if not path.exists():
        return None
    return _read_yaml(path)


def _discover(template_root: Path) -> list[Path]:
    """模板目录 = 含 template.yaml 的子目录。"""
    return sorted(p.parent for p in template_root.glob("*/template.yaml"))


def _structure_check(dir_path: Path, template: dict[str, Any]) -> list[str]:
    """返回结构问题列表（空表示通过）。"""
    problems: list[str] = []

    # 1) template.yaml 基本结构 + 引用文件存在
    try:
        descriptor = TemplateDescriptor.model_validate(template)
    except ValidationError as exc:
        problems.append(f"template.yaml 结构非法: {exc}")
        return problems
    for key, rel in descriptor.files.model_dump().items():
        if not (dir_path / rel).exists():
            problems.append(f"template.yaml 声明的 {key} 文件不存在: {rel}")

    # 2) rules.yaml：meta 合法 + 每个业务码节点可解析 + 必备子字段
    try:
        rules_doc = _read_yaml_if_exists(dir_path / descriptor.files.rules) or {}
        RulesMeta.model_validate(rules_doc.get("meta", {}))
    except ValidationError as exc:
        problems.append(f"rules.yaml: {exc}")
        rules_doc = {}
    for code, body in rules_doc.items():
        if code == "meta" or not isinstance(body, dict):
            continue
        try:
            node = NodeDef.model_validate(body)
        except ValidationError as exc:
            problems.append(f"rules.yaml 节点 {code} 非法: {exc}")
            continue
        if node.type == "predicate" and not node.operands:
            problems.append(f"rules.yaml 节点 {code}: predicate 缺 operands")
        if node.type in ("rule", "conclusion") and not node.children:
            problems.append(f"rules.yaml 节点 {code}: {node.type} 缺 children")

    # 3) parameters.yaml：quantities + thresholds
    params_doc = _read_yaml_if_exists(dir_path / descriptor.files.parameters) or {}
    if not params_doc.get("quantities"):
        problems.append("parameters.yaml 缺 quantities")
    if not params_doc.get("thresholds"):
        problems.append("parameters.yaml 缺 thresholds")

    # 4) style.yaml：result_style 三态齐全
    try:
        style_doc = _read_yaml_if_exists(dir_path / descriptor.files.style) or {}
        StyleResult.model_validate(style_doc.get("result_style", {}))
    except ValidationError as exc:
        problems.append(f"style.yaml result_style 非法: {exc}")

    return problems


def _parse_nodes(rules_doc: dict[str, Any]) -> dict[str, NodeDef]:
    return {
        code: NodeDef.model_validate(body)
        for code, body in rules_doc.items()
        if code != "meta" and isinstance(body, dict)
    }


def _load_one(dir_path: Path) -> TemplateConfig:
    template = _read_yaml(dir_path / "template.yaml")
    problems = _structure_check(dir_path, template)
    if problems:
        raise TemplateStructureError(dir_path.name, problems)

    files = template["files"]
    rules_doc = _read_yaml(dir_path / files["rules"])
    cfg = TemplateConfig(
        template_id=template["template"]["id"],
        path=dir_path,
        template=template,
        rules=rules_doc,
        parameters=_read_yaml(dir_path / files["parameters"]),
        style=_read_yaml(dir_path / files["style"]),
        nodes=_parse_nodes(rules_doc),
    )
    pattern = template.get("node_code", {}).get("pattern")
    if pattern:
        cfg.node_code_pattern = pattern
    return cfg


def load_templates(template_root: str | Path) -> dict[str, TemplateConfig]:
    """加载根目录下所有模板；返回 {template_id: TemplateConfig}。"""
    root = Path(template_root)
    if not root.is_dir():
        raise TemplateNotFoundError(f"模板根目录不存在: {root}")
    registry: dict[str, TemplateConfig] = {}
    for dir_path in _discover(root):
        cfg = _load_one(dir_path)
        registry[cfg.template_id] = cfg
    if not registry:
        raise TemplateNotFoundError(f"未在 {root} 发现任何模板")
    return registry
