"""模板校验器数据结构单测（需求 §7，Phase 2 占位）。

说明：`TemplateValidator.validate()` 目前为 Phase 2 空壳（ISSUE-2.x 落地 V01-V31
前直接抛 NotImplementedError）。本文件覆盖已实现部分——`Level` / `Finding` /
`ValidationReport.ok` 的合法与非法（ERROR vs WARNING）分支，并以断言锁定
`validate()` 的未实现契约，避免被误当作已生效的 schema 校验。
"""
from __future__ import annotations

import pytest

from core.template_validator import (
    Finding,
    Level,
    TemplateValidator,
    ValidationReport,
)

# --- Level：字符串枚举 ---------------------------------------------------

def test_level_is_str_enum() -> None:
    assert isinstance(Level.ERROR, str)
    assert Level.ERROR == "error"
    assert Level.WARNING == "warning"


def test_level_from_value() -> None:
    assert Level("error") is Level.ERROR
    assert Level("warning") is Level.WARNING


# --- Finding：带 slots 的记录 --------------------------------------------

def test_finding_fields_and_default_node() -> None:
    f = Finding(code="V01", level=Level.ERROR, message="业务码重复")
    assert f.code == "V01"
    assert f.level is Level.ERROR
    assert f.message == "业务码重复"
    assert f.node is None  # 默认无归属节点


def test_finding_uses_slots_rejects_dynamic_attr() -> None:
    f = Finding(code="V10", level=Level.WARNING, message="孤立节点", node="P003")
    assert f.node == "P003"
    assert not hasattr(f, "__dict__")  # slots=True：不存在实例 __dict__
    with pytest.raises(AttributeError):
        f.unexpected_field = 1  # 禁止动态加字段


# --- ValidationReport.ok：合法 / 非法分支 --------------------------------

def _f(level: Level) -> Finding:
    return Finding(code="V20", level=level, message="msg")


def test_empty_report_is_ok() -> None:
    assert ValidationReport().ok is True


def test_warning_only_report_is_ok() -> None:
    report = ValidationReport(findings=[_f(Level.WARNING), _f(Level.WARNING)])
    assert report.ok is True


def test_single_error_report_is_not_ok() -> None:
    report = ValidationReport(findings=[_f(Level.ERROR)])
    assert report.ok is False


def test_mixed_report_is_not_ok_when_any_error() -> None:
    report = ValidationReport(
        findings=[_f(Level.WARNING), _f(Level.ERROR), _f(Level.WARNING)]
    )
    assert report.ok is False


def test_default_findings_not_shared_between_reports() -> None:
    a, b = ValidationReport(), ValidationReport()
    a.findings.append(_f(Level.ERROR))
    assert a.findings is not b.findings  # default_factory 每次新建列表
    assert b.ok is True


# --- TemplateValidator：Phase 2 契约锁定 ---------------------------------

def test_validate_not_implemented_in_phase2_stub() -> None:
    validator = TemplateValidator()
    with pytest.raises(NotImplementedError):
        validator.validate({}, None)
