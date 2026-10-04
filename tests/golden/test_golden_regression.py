"""ISSUE-1.10 · Phase 1 黄金端到端回归。

固化「校验→求值→着色/回写」的完整快照（以业务码为主键，与随机 mxCell.id 无关），
作为 CI 门禁。可用 `pytest --update-golden` 在确认变更后重新生成。
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.golden._snapshot import golden_file, snapshot_for, write_golden

TEMPLATE_ID = "oil_fracturing_v1"


def test_golden_regression(templates_root: Path, update_golden: bool) -> None:
    snap = snapshot_for(templates_root, TEMPLATE_ID)
    if update_golden:
        write_golden(snap, TEMPLATE_ID)
    gf = golden_file(TEMPLATE_ID)
    assert gf.exists(), "缺少黄金快照基准：运行 pytest --update-golden 生成"
    expected = json.loads(gf.read_text(encoding="utf-8"))
    assert snap == expected


def test_snapshot_invariants(templates_root: Path) -> None:
    snap = snapshot_for(templates_root, TEMPLATE_ID)
    assert snap["template_id"] == TEMPLATE_ID
    assert snap["root_state"] == "FALSE"                    # 最终结论 C001
    assert len(snap["node_states"]) == 37                   # 全部业务码节点
    assert set(snap["node_states"].values()) <= {"TRUE", "FALSE", "UNKNOWN"}
    assert "UNKNOWN" not in snap["node_states"].values()    # 黄金样例全链路无缺失
    # 结果色仅来自 style 三色，未在代码里硬编码其它色
    assert set(snap["colors"].values()) <= {"#00B050", "#FF0000", "#FFFF00"}
    # 值注释覆盖全部输入 P 节点
    assert set(snap["annotations"]) == {c for c in snap["node_states"] if c.startswith("P")}
