"""多示例端到端回归（缺失 / 非法 / 极简 输入）。

验证需求 §9.3「非法/缺失输入不抛错、相应节点判 UNKNOWN」与本阶段新约定
「仅 P/C 施加结果色，R(关系)节点保持模板原样」、「阈值占位符替换只依赖
阈值表(§6.6 改版)，与实测值无关」在多场景下同时成立。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.golden._snapshot import run_bundle

TEMPLATE_ID = "oil_fracturing_v1"
YELLOW, GREEN, RED = "#FFFF00", "#00B050", "#FF0000"
# 描述中含"已定义阈值符号"的 P 节点集（§6.6 改版：替换只依赖阈值表，与实测值无关）
SUBSTITUTED_P = {"P001", "P002", "P005", "P006", "P007", "P008", "P009", "P013", "P015"}


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
    assert len(colors) == 24
    assert set(colors.values()) <= {YELLOW, GREEN, RED}
    # 描述补丁只发生在 P 节点（predicate_only）
    assert all(code.startswith("P") for code in snap["annotations"])


def test_missing_partial_input(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_missing")
    _assert_common_invariants(snap)

    # 完全省略的 P006/P003：判 UNKNOWN、黄底；但阈值替换与取值无关，照常生效
    for code in ("P006", "P003"):
        assert snap["node_states"][code] == "UNKNOWN"
        assert snap["colors"][code] == YELLOW
    assert "x12" not in snap["annotations"]["P006"]          # 占位符→默认阈值数值

    # 提供且成立的 P002：TRUE、绿底；描述 "&gt; x1 m" 已换成默认阈值 "&gt; 5 m"
    assert snap["node_states"]["P002"] == "TRUE"
    assert snap["colors"]["P002"] == GREEN
    assert "x1 " not in snap["annotations"]["P002"] and "5 m" in snap["annotations"]["P002"]

    # 缺操作数被记为 missing，且不影响服务成功返回
    kinds = {i["kind"] for i in snap["issues"]}
    assert "missing" in kinds
    assert any(i["location"].startswith("P001") for i in snap["issues"])
    # 关键路径缺数据 → 根结论无法判定为真，落到 UNKNOWN
    assert snap["root_state"] == "UNKNOWN"


def test_invalid_values_are_rejected_not_fatal(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_invalid")
    _assert_common_invariants(snap)

    kinds = {i["kind"] for i in snap["issues"]}
    assert {"range", "type"} <= kinds                        # 越界 + 非法类型均被拦截

    # 越界/非数值/非法布尔 → 相关节点 UNKNOWN；实测值从不进描述（回写已退役）
    assert snap["node_states"]["P001"] == "UNKNOWN"          # reservoir_layers=150 越界
    assert snap["node_states"]["P006"] == "UNKNOWN"          # oil_diff="abc" 非数值
    assert snap["node_states"]["P016"] == "UNKNOWN"          # casing_damage="maybe" 非法布尔
    assert snap["node_states"]["P002"] == "TRUE"             # 合法项不受影响
    assert "150" not in snap["annotations"].get("P001", "")  # 非法值绝不进描述
    assert "abc" not in "".join(snap["annotations"].values())
    assert "x12" not in snap["annotations"]["P006"]          # 替换只依阈值表，与非法值无关
    assert snap["root_state"] == "UNKNOWN"


def test_minimal_all_unknown(templates_root: Path) -> None:  # noqa: ANN001
    snap = _run(templates_root, "sample_request_minimal")
    _assert_common_invariants(snap)

    assert set(snap["node_states"].values()) == {"UNKNOWN"}  # 空输入 → 全 UNKNOWN
    assert set(snap["colors"].values()) == {YELLOW}          # P/C 全黄
    # 阈值替换与取值无关：空输入仍按 parameters.yaml 占位默认值把描述换成数值
    assert set(snap["annotations"]) == SUBSTITUTED_P
    assert snap["ok"] is True                                 # 未提供≠非法：无 issues
    assert snap["root_state"] == "UNKNOWN"


def test_all_meets_criteria_root_true(templates_root: Path) -> None:  # noqa: ANN001
    """全部达标：13 个 P 判真 → 沿 R→C 聚合使根结论 C001=TRUE。"""
    snap = _run(templates_root, "sample_request_true")
    _assert_common_invariants(snap)

    assert snap["root_state"] == "TRUE"
    # 决定性关键路径全真（P003/P008 由假翻真后打通 C011/C012）
    for code in ("C001", "R01", "C011", "C012", "C013", "C014", "P003", "P008"):
        assert snap["node_states"][code] == "TRUE", code
    # 13 个 P 全真、全绿；无 issue
    p_codes = [c for c in snap["node_states"] if c.startswith("P")]
    assert len(p_codes) == 13
    assert all(snap["node_states"][c] == "TRUE" for c in p_codes)
    assert all(snap["colors"][c] == GREEN for c in p_codes)
    assert set(snap["annotations"]) == SUBSTITUTED_P          # 替换覆盖面与取值无关
    assert snap["ok"] is True


@pytest.mark.parametrize(
    "name",
    ["sample_request_missing", "sample_request_invalid", "sample_request_minimal", "sample_request_true"],
)
def test_samples_run_without_error(templates_root: Path, name: str) -> None:  # noqa: ANN001
    """任何示例都应跑通并给出根状态（鲁棒性兜底）。"""
    snap = _run(templates_root, name)
    assert snap["root_state"] in {"TRUE", "FALSE", "UNKNOWN"}
    assert len(snap["node_states"]) == 37                     # 三态覆盖全部业务码（含 R）
