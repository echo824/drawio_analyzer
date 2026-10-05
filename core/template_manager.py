"""模板管理器（需求 §10.5、§11、§7.5）。

封装 config_loader 注册表 + **解析后 TemplateModel 缓存**（§10.4），供 API 复用，
避免每请求重复解析原始 .drawio；reload() 时清缓存。

**先校验后入缓（§7.5）**：`get_model()` 首次访问时 parse → validate，
仅当无错误级 Finding 才缓存并返回；否则抛 `TemplateValidationError`（不入缓），
若存在上一份有效模型（跨 reload 保留于 `_last_valid`）则回退继续服务。
告警级 Finding（如 V02/V10/V13/V31）仅记日志，不阻断入缓（待确认项 G3/G2）。

Phase 3：增加文件监控 reload、多版本（需求 §10.6、§11.3）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from config_loader import ConfigError, TemplateConfig, load_templates

from .drawio_parser import DrawioParseError, DrawioParser
from .models import TemplateModel
from .template_validator import Finding, Level, TemplateValidator, ValidationReport

FLOW_FILENAME = "flow.drawio"

logger = logging.getLogger("oil_fracturing.template")


class TemplateValidationError(ConfigError):
    """模板未通过 §7 校验：拒绝入缓、保留上一份有效模板、返回明确错误。"""

    def __init__(self, template_id: str, problems: list[str]) -> None:
        self.template_id = template_id
        self.problems = problems
        super().__init__(f"模板 '{template_id}' 校验未通过: {'; '.join(problems)}")


class TemplateManager:
    def __init__(self, templates_root: str | Path) -> None:
        self.root = Path(templates_root)
        self._registry: dict[str, TemplateConfig] = {}
        self._models: dict[str, TemplateModel] = {}      # 已校验通过的解析缓存（§10.4）
        self._last_valid: dict[str, TemplateModel] = {}  # 最近有效模型，跨 reload 保留作回退（§7.5）
        self.reload()

    @property
    def registry(self) -> dict[str, TemplateConfig]:
        return self._registry

    def reload(self) -> None:
        """发现并加载全部模板（结构校验失败即抛出）；清解析缓存以强制重校验。

        `_last_valid` 不清，确保坏模板 reload 后仍能回退到上一份有效模型。
        """
        self._registry = load_templates(self.root)
        self._models.clear()

    def get(self, template_id: str) -> TemplateConfig:
        return self._registry[template_id]

    def get_model(self, template_id: str) -> TemplateModel:
        """取解析后的模板模型；首次访问解析+校验后缓存，之后复用（原始 .drawio 只读）。

        未通过校验（错误级）→ 拒绝入缓；若有上一份有效模型则回退，否则抛错。
        """
        cached = self._models.get(template_id)
        if cached is not None:
            return cached
        try:
            model = self._load_and_validate(template_id)
        except TemplateValidationError:
            fallback = self._last_valid.get(template_id)
            if fallback is not None:
                logger.warning("模板 %s 新版本未通过校验，回退上一份有效模板", template_id)
                self._models[template_id] = fallback
                return fallback
            raise
        self._models[template_id] = model
        self._last_valid[template_id] = model
        return model

    def validate_all(self) -> dict[str, ValidationReport]:
        """启动/巡检：对每个模板返回校验报告（解析失败合成错误级）；不抛异常、不缓存。"""
        reports: dict[str, ValidationReport] = {}
        for template_id, cfg in self._registry.items():
            try:
                model = DrawioParser(cfg.node_code_pattern).parse(cfg.path / FLOW_FILENAME)
            except DrawioParseError as exc:
                code = "V31" if ("V31" in str(exc) or "压缩" in str(exc)) else "V30"
                reports[template_id] = ValidationReport([Finding(code, Level.ERROR, str(exc))])
                continue
            reports[template_id] = TemplateValidator().validate(model, cfg)
        return reports

    # ── 内部 ──────────────────────────────────────────────
    def _load_and_validate(self, template_id: str) -> TemplateModel:
        cfg = self.get(template_id)
        flow_path: Path = cfg.path / FLOW_FILENAME
        try:
            model = DrawioParser(cfg.node_code_pattern).parse(flow_path)
        except DrawioParseError as exc:
            logger.error("模板 %s 解析失败（拒绝入缓）: %s", template_id, exc)
            raise TemplateValidationError(template_id, [str(exc)]) from exc

        report = TemplateValidator().validate(model, cfg)
        for finding in report.warnings():
            logger.warning("模板 %s 校验告警 [%s] %s", template_id, finding.code, finding.message)
        if not report.ok:
            problems = [f"{f.code} {f.message}" for f in report.errors()]
            logger.error("模板 %s 校验未通过（拒绝入缓）: %s", template_id, "; ".join(problems))
            raise TemplateValidationError(template_id, problems)
        return model

    def flow_path(self, template_id: str) -> Path:
        return self.get(template_id).path / FLOW_FILENAME

    def list_ids(self) -> list[str]:
        return sorted(self._registry)


__all__ = ["TemplateManager", "TemplateValidationError", "FLOW_FILENAME"]
