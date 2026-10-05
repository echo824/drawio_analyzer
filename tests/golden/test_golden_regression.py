"""ISSUE-1.10 · Phase 1 黄金端到端回归。

固化「校验→求值→着色/阈值替换」的完整快照（以业务码为主键，与随机 mxCell.id 无关），
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
    assert len(snap["node_states"]) == 39                   # 全部业务码节点（1005 版）
    assert set(snap["node_states"].values()) <= {"TRUE", "FALSE", "UNKNOWN"}
    assert "UNKNOWN" not in snap["node_states"].values()    # 黄金样例全链路无缺失
    # 结果色仅来自 style 三色，未在代码里硬编码其它色
    assert set(snap["colors"].values()) <= {"#00B050", "#FF0000", "#FFFF00"}
    # 阈值替换覆盖“描述含已定义阈值符号”的全部 P 节点（与实测值无关）：
    # P010/P011/P016 等描述已无占位符 → 不产生补丁
    assert set(snap["annotations"]) == {
        "P001", "P002", "P005", "P006", "P007", "P008", "P009", "P013", "P015",
    }
    # 占位符替换后的描述不应再含已定义符号名（括号内解释文字除外）
    assert all("x13" not in v and "x12" not in v and "x22" not in v
               for v in snap["annotations"].values())
