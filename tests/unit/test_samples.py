"""多示例端到端回归（缺失 / 非法 / 极简 / 全达标 输入）。

验证需求 §9.3「非法/缺失输入不抛错、相应节点判 UNKNOWN」与本阶段约定
「仅 P/C 施加结果色，R(关系)节点保持模板原样」在多场景下同时成立。
描述补丁 = §6.6 阈值替换 + §4.6 值回写（共存）。生产模板已数字直写→无替换；
值回写仅对「已在 node_values 提供且可解析、且描述含对应运算符」的 P 节点生成，
故 annotations 的覆盖面随各示例传值而变（非恒空）。§6.6 替换机制本身的集成
覆盖见合成夹具（tests/unit/test_renderer_annotation + tests/conftest:symbol_flow_path）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.golden._snapshot import run_bundle

TEMPLATE_ID = "oil_fracturing_v1"
YELLOW, GREEN, RED = "#FFFF00", "#00B050", "#FF0000"
# 全达标示例：14 个 P 节点均传有效值→全部值回写（P004/P014 不存在）。
ALL_P: set[str] = {
    "P001", "P002", "P003", "P005", "P006", "P007", "P008",
    "P009", "P010", "P011", "P012", "P013", "P015", "P016",
}


def _run(templates_root: Path, name: str) -> dict[str, Any]:
    payload = json.loads(
        (templates_root / TEMPLATE_ID / f"{name}.json").read_text(encoding="utf-8")
    )
    return run_bundle(templates_root, TEMPLATE_ID, payload)


def _assert_common_invariants(snap: dict[str, Any]) -> None:
    """三例共有的不变量。"""
    colors = snap["colors"]
    states = snap["node_states"]
    # 三态闭合
    assert set(states.values()) <= {"TRUE", "FALSE", "UNKNOWN"}
    # R(关系)节点从不入色，且 P/C 全部入色（每个都有求值态，含 UNKNOWN→黄）
    assert not [c for c in colors if c.startswith("R")]
    assert len(colors) == 25
    assert set(colors.values()) <= {YELLOW, GREEN, RED}
    # 描述补丁只发生在 P 节点（predicate_only）
    assert all(code.startswith("P") for code in snap["annotations"])


def test_missing_partial_input(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_missing")
    _assert_common_invariants(snap)

    # 完全省略的 P006/P003：判 UNKNOWN、黄底
    for code in ("P006", "P003"):
        assert snap["node_states"][code] == "UNKNOWN"
        assert snap["colors"][code] == YELLOW
    # 提供且成立的 P002：TRUE、绿底
    assert snap["node_states"]["P002"] == "TRUE"
    assert snap["colors"]["P002"] == GREEN
    # 缺操作数被记为 missing，且不影响服务成功返回
    kinds = {i["kind"] for i in snap["issues"]}
    assert "missing" in kinds
    assert any(i["location"].startswith("P001") for i in snap["issues"])
    # 值回写仅覆盖本示例实际传值的 P 节点（完全省略的 P006/P003 不在其中）
    assert set(snap["annotations"]) == {"P001", "P002", "P005", "P010", "P016"}
    assert "P009" not in snap["annotations"]                    # 未传→不回写
    # 关键路径缺数据 → 根结论无法判定为真，落到 UNKNOWN
    assert snap["root_state"] == "UNKNOWN"


def test_invalid_values_are_rejected_not_fatal(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_invalid")
    _assert_common_invariants(snap)

    kinds = {i["kind"] for i in snap["issues"]}
    assert {"range", "type"} <= kinds                        # 越界 + 非法类型均被拦截

    # 越界/非数值/非法布尔 → 相关节点 UNKNOWN；非法量不注入（仅保留槽位）
    assert snap["node_states"]["P001"] == "UNKNOWN"          # reservoir_layers=150 越界
    assert snap["node_states"]["P006"] == "UNKNOWN"          # oil_diff="abc" 非数值
    assert snap["node_states"]["P016"] == "UNKNOWN"          # casing_damage="maybe" 非法布尔
    assert snap["node_states"]["P002"] == "TRUE"             # 合法项不受影响
    # 仅合法传值且含运算符的 P 节点被回写（非法量不注入）
    assert set(snap["annotations"]) == {"P001", "P002", "P016"}
    # 位置对齐：P001 前置量越界(不插)但保留槽位→后置量（12.3）仍落在折算厚度运算符前
    p001 = snap["annotations"]["P001"]
    assert "层数 &gt;" in p001 and "（12.3） &gt; 5m" in p001
    assert "层数（" not in p001
    assert snap["root_state"] == "UNKNOWN"


def test_minimal_all_unknown(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_minimal")
    _assert_common_invariants(snap)

    assert set(snap["node_states"].values()) == {"UNKNOWN"}  # 空输入 → 全 UNKNOWN
    assert set(snap["colors"].values()) == {YELLOW}          # P/C 全黄
    # 未提供任何 node_values → 无任何回写（与是否含运算符无关）
    assert snap["annotations"] == {}
    assert snap["ok"] is True                                 # 未提供≠非法：无 issues
    assert snap["root_state"] == "UNKNOWN"


def test_all_meets_criteria_root_true(templates_root: Path) -> None:  # noqa: ANN001
    """全部达标：14 个 P 判真（1005 版含 P012）→ 沿 R→C 聚合使根结论 C001=TRUE。"""
    snap = _run(templates_root, "sample_request_true")
    _assert_common_invariants(snap)

    assert snap["root_state"] == "TRUE"
    # 决定性关键路径全真（P003/P008 由假翻真后打通 C011/C012）
    for code in ("C001", "R01", "C011", "C012", "C013", "C014", "P003", "P008"):
        assert snap["node_states"][code] == "TRUE", code
    # 14 个 P 全真、全绿；无 issue
    p_codes = [c for c in snap["node_states"] if c.startswith("P")]
    assert len(p_codes) == 14
    assert all(snap["node_states"][c] == "TRUE" for c in p_codes)
    assert all(snap["colors"][c] == GREEN for c in p_codes)
    # 14 个 P 均传有效值→全部回写；用户重点例 P009
    assert set(snap["annotations"]) == ALL_P
    assert snap["annotations"]["P009"] == "P009 本井日产油（2） &lt; 3 t"
    assert snap["ok"] is True


@pytest.mark.parametrize(
    "name",
    ["sample_request_missing", "sample_request_invalid", "sample_request_minimal", "sample_request_true"],
)
def test_samples_run_without_error(templates_root: Path, name: str) -> None:  # noqa: ANN001
    """任何示例都应跑通并给出根状态（鲁棒性兜底）。"""
    snap = _run(templates_root, name)
    assert snap["root_state"] in {"TRUE", "FALSE", "UNKNOWN"}
    assert len(snap["node_states"]) == 39                     # 三态覆盖全部业务码（含 R；1005 版 +P012/R12-3）
