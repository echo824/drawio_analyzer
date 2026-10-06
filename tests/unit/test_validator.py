"""ISSUE-1.3 输入 Schema 校验单测（需求 §5.5、§2.1）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_loader import load_templates
from core.validator import (
    InputValidator,
    NodeValuesResult,
    ValidationError,
)

TEMPLATE_ID = "oil_fracturing_v1"
KNOWN_CODES = {"P001", "P002", "P003", "P005", "P006", "P016"}


@pytest.fixture()
def cfg(templates_root: Path):  # noqa: ANN201
    return load_templates(templates_root)[TEMPLATE_ID]


@pytest.fixture()
def validator(cfg) -> InputValidator:  # noqa: ANN001
    return InputValidator.from_config(cfg)


@pytest.fixture()
def sample_request(templates_root: Path) -> dict:  # noqa: ANN201
    path = templates_root / TEMPLATE_ID / "sample_request.json"
    return json.loads(path.read_text(encoding="utf-8"))


def issue_kinds(result: NodeValuesResult) -> set[str]:
    return {i.kind for i in result.issues}


# ── 结构归一 ────────────────────────────────────────────
def test_scalar_normalized_to_single_operand(validator: InputValidator) -> None:
    # P006 单操作数：标量 20 → {oil_diff: 20}
    result = validator.validate_node_values({"P006": 20})
    assert result.values["P006"] == {"oil_diff": 20}
    assert result.ok


def test_output_structure_is_code_plus_operand_name(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": {"reservoir_layers": 5, "converted_thickness": 12}})
    assert set(result.values["P001"]) == {"reservoir_layers", "converted_thickness"}


def test_scalar_rejected_for_multi_operand_node(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": 5})   # P001 有两个操作数
    assert "type" in issue_kinds(result)
    assert result.values["P001"]["reservoir_layers"] is None


# ── 数组按位置映射（推荐契约）──────────────────
def test_array_positional_maps_in_operand_order(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": [5, 12.3]})
    assert result.values["P001"] == {"reservoir_layers": 5, "converted_thickness": 12.3}
    assert result.ok


def test_array_short_tail_marks_missing(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": [5]})   # 只给第一位
    assert result.values["P001"]["reservoir_layers"] == 5
    assert result.values["P001"]["converted_thickness"] is None
    assert any(i.location == "P001.converted_thickness" and i.kind == "missing" for i in result.issues)


def test_array_longer_than_operands_marks_type(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": [5, 12.3, 99]})
    assert "type" in issue_kinds(result)                     # 长度超出
    assert result.values["P001"]["reservoir_layers"] == 5    # 多余位被忽略


def test_array_single_operand_and_bool(validator: InputValidator) -> None:
    assert validator.validate_node_values({"P006": [20]}).values["P006"] == {"oil_diff": 20}
    r = validator.validate_node_values({"P016": [False]})   # 单操作数数组：[套管损坏]
    assert r.values["P016"] == {"casing_damage": False}


def test_array_positional_invalid_still_named(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P016": ["maybe"]})   # 单操作数数组，位置 0 → casing_damage 非法布尔
    assert result.values["P016"]["casing_damage"] is None
    assert any(i.location == "P016.casing_damage" and i.kind == "type" for i in result.issues)


# ── 合法样例全通过 ──────────────────────────────────────
def test_sample_request_passes_clean(validator: InputValidator, sample_request: dict) -> None:
    result = validator.validate_node_values(sample_request["node_values"])
    assert result.ok, [str(i) for i in result.issues]
    assert result.values["P005"]["water_cut"] == 40
    assert result.values["P016"]["casing_damage"] is False
    assert result.values["P001"]["converted_thickness"] == 12.3
    assert result.values["P001"]["reservoir_layers"] == 5


# ── 类型不符 / 越界 → 哨兵 None + issue（不抛错）─────────
def test_wrong_type_marked_none(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": {"reservoir_layers": "abc", "converted_thickness": 12}})
    assert result.values["P001"]["reservoir_layers"] is None
    assert result.values["P001"]["converted_thickness"] == 12
    assert any(i.location == "P001.reservoir_layers" and i.kind == "type" for i in result.issues)


def test_out_of_range_marked_none(validator: InputValidator) -> None:
    # water_cut 允许 [0,100]
    result = validator.validate_node_values({"P005": {"water_cut": 130}})
    assert result.values["P005"]["water_cut"] is None
    assert "range" in issue_kinds(result)


def test_integer_rejects_fractional(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": {"reservoir_layers": 5.5, "converted_thickness": 3}})
    assert result.values["P001"]["reservoir_layers"] is None
    assert "type" in issue_kinds(result)


def test_integer_accepts_integral_float_and_numeric_string(validator: InputValidator) -> None:
    result = validator.validate_node_values(
        {"P001": {"reservoir_layers": 5.0, "converted_thickness": "12"}}
    )
    assert result.values["P001"]["reservoir_layers"] == 5
    assert result.values["P001"]["converted_thickness"] == 12.0
    assert result.ok


# ── 布尔校验（casing_damage）─────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(False, False), (True, True), ("true", True), ("false", False), (1, True), (0, False)],
)
def test_boolean_coercion(validator: InputValidator, raw, expected) -> None:  # noqa: ANN001
    result = validator.validate_node_values(
        {"P016": {"casing_damage": raw}}
    )
    assert result.values["P016"]["casing_damage"] is expected


def test_boolean_invalid_marked_none(validator: InputValidator) -> None:
    result = validator.validate_node_values(
        {"P016": {"casing_damage": "maybe"}}
    )
    assert result.values["P016"]["casing_damage"] is None
    assert "type" in issue_kinds(result)


# ── 缺失 / 未知操作数 ───────────────────────────────────
def test_missing_operand_becomes_none_not_error(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": {"reservoir_layers": 5}})  # 缺 converted_thickness
    assert result.values["P001"]["converted_thickness"] is None
    assert any(i.location == "P001.converted_thickness" and i.kind == "missing" for i in result.issues)


def test_unknown_operand_flagged(validator: InputValidator) -> None:
    result = validator.validate_node_values({"P001": {"reservoir_layers": 5, "converted_thickness": 3, "foo": 1}})
    assert "unknown_operand" in issue_kinds(result)
    assert "foo" not in result.values["P001"]      # 仅保留规则已知操作数


def test_unknown_node_flagged(validator: InputValidator) -> None:
    result = validator.validate_node_values({"R01": {"x": 1}})   # R 节点非 predicate
    assert "unknown_node" in issue_kinds(result)
    assert "R01" not in result.values


# ── thresholds 数值校验（basis 已随右值阈值化退役）───────
def test_validate_thresholds_numeric(validator: InputValidator) -> None:
    thr, thr_issues = validator.validate_thresholds({"x13": 60, "x_bad": "abc"})
    assert thr["x13"] == 60
    assert thr["x_bad"] is None and thr_issues[0].kind == "type"


# ── strict 模式：required 缺失才抛 ──────────────────────
def test_strict_raises_only_when_required_missing(cfg) -> None:  # noqa: ANN001
    strict = InputValidator(cfg.nodes, cfg.quantities, cfg.thresholds, strict=True)
    # 当前 schema 无 required 标记 → 即便缺失也不抛
    res = strict.validate_node_values({"P001": {"reservoir_layers": 5}})
    assert res.ok is False                     # 仍有 missing issue
    # 手工注入一个 required 量触发抛错
    cfg.quantities["converted_thickness"]["required"] = True
    try:
        with pytest.raises(ValidationError):
            strict.validate_node_values({"P001": {"reservoir_layers": 5}})
    finally:
        cfg.quantities["converted_thickness"].pop("required", None)
