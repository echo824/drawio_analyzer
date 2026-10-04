"""模板管理器（需求 §10.5、§11）。Phase 3+ 落地热部署/版本。

Phase 1：封装 config_loader 注册表 + **解析后的 TemplateModel 缓存**（§10.4），
供 API 复用，避免每请求重复解析原始 .drawio；reload() 时清缓存。
Phase 3：增加文件监控 reload、多版本（需求 §10.6、§11.3）。
"""
from __future__ import annotations

from pathlib import Path

from config_loader import TemplateConfig, load_templates

from .drawio_parser import DrawioParser
from .models import TemplateModel

FLOW_FILENAME = "flow.drawio"


class TemplateManager:
    def __init__(self, templates_root: str | Path) -> None:
        self.root = Path(templates_root)
        self._registry: dict[str, TemplateConfig] = {}
        self._models: dict[str, TemplateModel] = {}   # 解析缓存（§10.4）
        self.reload()

    @property
    def registry(self) -> dict[str, TemplateConfig]:
        return self._registry

    def reload(self) -> None:
        """发现并加载全部模板（结构校验失败即抛出）；清空解析缓存。"""
        self._registry = load_templates(self.root)
        self._models.clear()

    def get(self, template_id: str) -> TemplateConfig:
        return self._registry[template_id]

    def get_model(self, template_id: str) -> TemplateModel:
        """取解析后的模板模型；首次访问解析并缓存，之后复用（原始 .drawio 只读）。"""
        cached = self._models.get(template_id)
        if cached is not None:
            return cached
        cfg = self.get(template_id)
        flow_path: Path = cfg.path / FLOW_FILENAME
        model = DrawioParser(cfg.node_code_pattern).parse(flow_path)
        self._models[template_id] = model
        return model

    def flow_path(self, template_id: str) -> Path:
        return self.get(template_id).path / FLOW_FILENAME

    def list_ids(self) -> list[str]:
        return sorted(self._registry)


__all__ = ["TemplateManager", "FLOW_FILENAME"]
