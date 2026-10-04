"""配置加载器单测（Phase 0 验收）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from config_loader import load_templates
from config_loader.exceptions import TemplateStructureError


def test_load_discovers_oil_fracturing(templates_root: Path) -> None:
    registry = load_templates(templates_root)
    assert "oil_fracturing_v1" in registry


def test_parsed_nodes_and_meta(templates_root: Path) -> None:
    cfg = load_templates(templates_root)["oil_fracturing_v1"]
    # rules.yaml 至少含 P001 与根 C001
    assert cfg.nodes["P001"].type == "predicate"
    assert cfg.nodes["C001"].is_root is True
    assert cfg.rules_meta.default_aggregate == "AND"
    # 参数与阈值可读
    assert "converted_thickness" in cfg.quantities
    assert cfg.thresholds["x_p005_y"]["value"] == 8


def test_node_code_pattern_from_template(templates_root: Path) -> None:
    cfg = load_templates(templates_root)["oil_fracturing_v1"]
    assert cfg.node_code_pattern.startswith("^")


def test_structure_error_on_bad_template(tmp_path: Path) -> None:
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "template.yaml").write_text(
        "template:\n  id: broken\nfiles:\n  flow: flow.drawio\n"
        "  rules: rules.yaml\n  parameters: parameters.yaml\n  style: style.yaml\n",
        encoding="utf-8",
    )
    # 缺少被引用文件 → 结构校验应失败
    with pytest.raises(TemplateStructureError):
        load_templates(tmp_path)
