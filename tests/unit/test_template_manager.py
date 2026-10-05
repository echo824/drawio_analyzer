"""TemplateManager「先校验后入缓」集成测试（需求 §7.5，Phase 2 commit C）。

用 monkeypatch 注入校验/解析失败，验证：错误级 → 不入缓、有上一份有效则回退、
无上一份则抛 TemplateValidationError；真实模板应校验通过并复用缓存实例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.drawio_parser import DrawioParseError, DrawioParser
from core.template_manager import TemplateManager, TemplateValidationError
from core.template_validator import Finding, Level, TemplateValidator, ValidationReport

TEMPLATE_ID = "oil_fracturing_v1"


def _boom_validate(self: TemplateValidator, model: object, cfg: object) -> ValidationReport:
    return ValidationReport([Finding("V99", Level.ERROR, "注入的校验错误")])


def _boom_parse(self: DrawioParser, path: object) -> None:
    raise DrawioParseError("注入的解析失败（安全/压缩）")


# ── 真实模板：通过校验、缓存复用、validate_all 巡检 ──────
def test_real_template_validates_and_caches(templates_root: Path) -> None:
    mgr = TemplateManager(templates_root)
    m1 = mgr.get_model(TEMPLATE_ID)
    m2 = mgr.get_model(TEMPLATE_ID)
    assert m1 is m2                                        # 同一实例，避免重复解析
    assert len(m1.code_map) == 37
    assert mgr.validate_all()[TEMPLATE_ID].ok              # 仅告警，整体通过


# ── 新版本校验失败：拒绝入缓但回退上一份有效 ─────────────
def test_rejects_new_version_but_keeps_last_valid(
    templates_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = TemplateManager(templates_root)
    good = mgr.get_model(TEMPLATE_ID)                      # 成功入缓并记 _last_valid

    monkeypatch.setattr(TemplateValidator, "validate", _boom_validate)
    mgr.reload()                                           # 清 _models，保留 _last_valid
    assert mgr.get_model(TEMPLATE_ID) is good              # 回退上一份有效模板


# ── 首份即校验失败（无回退）：抛错、不入缓 ───────────────
def test_first_load_validation_failure_raises(
    templates_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = TemplateManager(templates_root)
    monkeypatch.setattr(TemplateValidator, "validate", _boom_validate)
    with pytest.raises(TemplateValidationError):
        mgr.get_model(TEMPLATE_ID)
    assert mgr._models.get(TEMPLATE_ID) is None            # 未通过校验不入缓


# ── 解析失败（V30/V31）：包装为 TemplateValidationError ──
def test_parse_error_wrapped_as_validation_error(
    templates_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = TemplateManager(templates_root)
    monkeypatch.setattr(DrawioParser, "parse", _boom_parse)
    with pytest.raises(TemplateValidationError):
        mgr.get_model(TEMPLATE_ID)


def test_parse_error_falls_back_to_last_valid(
    templates_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = TemplateManager(templates_root)
    good = mgr.get_model(TEMPLATE_ID)
    monkeypatch.setattr(DrawioParser, "parse", _boom_parse)
    mgr.reload()
    assert mgr.get_model(TEMPLATE_ID) is good              # 解析失败仍回退上一份有效


# ── validate_all：解析失败被合成错误级、不抛异常 ─────────
def test_validate_all_reports_parse_failure_without_raising(
    templates_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = TemplateManager(templates_root)
    monkeypatch.setattr(DrawioParser, "parse", _boom_parse)
    reports = mgr.validate_all()                           # 巡检不应抛出
    assert not reports[TEMPLATE_ID].ok
    assert reports[TEMPLATE_ID].errors()[0].code in {"V30", "V31"}   # 解析失败→合成安全级
