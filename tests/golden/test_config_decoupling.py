"""ISSUE-1.10 · 配置解耦验证。

在**临时副本**上修改 rules.yaml 算子 / parameters.yaml 阈值 / style.yaml 色值，
证明输出随之按预期变化（原始模板文件与黄金快照均不受影响）。
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from tests.golden._snapshot import golden_file, snapshot_for

TEMPLATE_ID = "oil_fracturing_v1"


def _mutated_root(base_root: Path, tmp_path: Path, edits: dict[str, Callable[[Any], None]]) -> Path:
    dst_dir = tmp_path / "templates" / TEMPLATE_ID
    shutil.copytree(base_root / TEMPLATE_ID, dst_dir)
    for fname, fn in edits.items():
        p = dst_dir / fname
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        fn(data)
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return tmp_path / "templates"


def test_style_color_drives_output(tmp_path: Path, templates_root: Path) -> None:
    base = snapshot_for(templates_root, TEMPLATE_ID)
    # colors 仅含 P/C（R 不着色）；从中取真/假节点
    true_nodes = [c for c in base["colors"] if base["node_states"][c] == "TRUE"]
    false_nodes = [c for c in base["colors"] if base["node_states"][c] == "FALSE"]
    assert true_nodes and false_nodes

    def edit(style: dict) -> None:
        style["result_style"]["TRUE"]["strokeColor"] = "#123456"     # stroke 通道：结果色=边线色

    snap = snapshot_for(_mutated_root(templates_root, tmp_path, {"style.yaml": edit}), TEMPLATE_ID)
    assert all(snap["colors"][c] == "#123456" for c in true_nodes)     # TRUE 色随 style
    assert all(snap["colors"][c] == base["colors"][c] for c in false_nodes)  # FALSE 不变
    assert snap["node_states"] == base["node_states"]                  # 颜色不改三态
    assert "R06" not in snap["colors"]                                 # R 始终不入色


def test_rules_operator_flips_state(tmp_path: Path, templates_root: Path) -> None:
    base = snapshot_for(templates_root, TEMPLATE_ID)
    assert base["node_states"]["P006"] == "TRUE"                       # 20 > 10

    def edit(rules: dict) -> None:
        rules["P006"]["operands"][0]["op"] = "<"

    snap = snapshot_for(_mutated_root(templates_root, tmp_path, {"rules.yaml": edit}), TEMPLATE_ID)
    assert snap["node_states"]["P006"] == "FALSE"                      # 20 < 10 不成立
    assert snap["node_states"]["P001"] == base["node_states"]["P001"]  # 旁路节点不受影响


def test_parameters_threshold_flips_state(tmp_path: Path, templates_root: Path) -> None:
    base = snapshot_for(templates_root, TEMPLATE_ID)
    assert base["node_states"]["P003"] == "FALSE"                      # 9.0 > x_p003(占位 9) 不成立

    def edit(params: dict) -> None:
        params["thresholds"]["x_p003"]["value"] = 8.0                  # 右值阈值化：改单值即翻态

    snap = snapshot_for(
        _mutated_root(templates_root, tmp_path, {"parameters.yaml": edit}), TEMPLATE_ID
    )
    assert snap["node_states"]["P003"] == "TRUE"


def test_original_files_untouched(templates_root: Path) -> None:
    # 基线快照与入库黄金文件一致 → 解耦测试未污染真实模板
    golden = json.loads(golden_file(TEMPLATE_ID).read_text(encoding="utf-8"))
    assert snapshot_for(templates_root, TEMPLATE_ID) == golden
