"""配置加载异常层次。"""


class ConfigError(Exception):
    """配置加载/结构校验的基础异常。"""


class TemplateNotFoundError(ConfigError):
    """请求的模板不存在。"""


class TemplateStructureError(ConfigError):
    """模板配置结构不合法（Phase 0 阶段的基本结构断言）。"""

    def __init__(self, template_id: str, problems: list[str]) -> None:
        self.template_id = template_id
        self.problems = problems
        detail = "; ".join(problems)
        super().__init__(f"模板 '{template_id}' 配置结构校验失败: {detail}")
