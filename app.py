"""Flask 入口（Phase 0 骨架）。

职责：
  - 应用工厂 + /health 健康检查
  - 启动时通过 TemplateManager 加载/结构校验/缓存解析 templates/ 下的所有模板
  - /api/v1/evaluate：解析→校验→求值→渲染 端到端（默认 HTML，可 result=json 返回结构化三态）

运行：
    flask --app app run  或  python app.py
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, Response, jsonify, request

from config_loader import ConfigError, TemplateNotFoundError
from core.expr import build_parameter_context
from core.renderer import Renderer
from core.rule_engine import RuleEngine
from core.template_manager import TemplateManager
from core.validator import InputValidator

TEMPLATES_ROOT = os.environ.get("TEMPLATES_ROOT", "templates")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("oil_fracturing")


def create_app(templates_root: str | os.PathLike[str] = TEMPLATES_ROOT) -> Flask:
    app = Flask(__name__)
    app.config["TEMPLATES_ROOT"] = str(templates_root)
    app.config["MANAGER"]: TemplateManager | None = None

    _init_manager(app)

    # ── 健康检查 ────────────────────────────────────────────
    @app.get("/health")
    def health():  # noqa: ANN202
        return jsonify({"status": "ok"})

    # ── 已加载模板清单（便于排障/验收）─────────────────────
    @app.get("/api/v1/templates")
    def list_templates():  # noqa: ANN202
        reg = app.config["MANAGER"].registry
        return jsonify(
            {
                "count": len(reg),
                "templates": [
                    {
                        "id": cfg.template_id,
                        "version": cfg.template["template"].get("version"),
                        "rules_version": cfg.rules_meta.rules_version,
                        "nodes": len(cfg.nodes),
                    }
                    for cfg in reg.values()
                ],
            }
        )

    # ── 评价接口：解析→校验→求值→渲染（需求 §9）─────────
    @app.post("/api/v1/evaluate")
    def evaluate():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        template_id = payload.get("template")
        if not template_id:
            return jsonify({"error": "invalid_request", "message": "缺少 'template' 字段"}), 400
        mgr = app.config["MANAGER"]
        if template_id not in mgr.registry:
            raise TemplateNotFoundError(template_id)

        data = _run_pipeline(mgr, template_id, payload)
        want_json = (
            request.args.get("result") == "json"
            or request.accept_mimetypes.best == "application/json"
        )
        if want_json:
            body = {
                **data["summary"],
                "validation": {"ok": data["ok"], "issues": data["issues"]},
            }
            return jsonify(body), 200
        return Response(data["html"], mimetype="text/html"), 200

    # ── 统一异常处理 ────────────────────────────────────────
    @app.errorhandler(TemplateNotFoundError)
    def _handle_not_found(exc: TemplateNotFoundError):  # noqa: ANN201
        return jsonify({"error": "template_not_found", "detail": str(exc)}), 404

    @app.errorhandler(ConfigError)
    def _handle_config_error(exc: ConfigError):  # noqa: ANN201
        return jsonify({"error": "template_unavailable", "detail": str(exc)}), 422

    return app


def _init_manager(app: Flask) -> None:
    """启动时构建 TemplateManager（加载 + 结构校验）。失败即记录并中断（fail-fast）。

    随后对每个模板做一次 §7 一致性预校验并记录日志（先校验后入缓的巡检；
    错误级模板不阻断其它模板加载，仅在其被请求时由 get_model 抛 422）。
    """
    root = Path(app.config["TEMPLATES_ROOT"])
    try:
        mgr = TemplateManager(root)
    except ConfigError as exc:
        logger.error("模板加载失败: %s", exc)
        raise
    app.config["MANAGER"] = mgr
    logger.info("已加载 %d 个模板: %s", len(mgr.registry), ", ".join(mgr.registry))
    _log_validation(mgr)


def _log_validation(mgr: TemplateManager) -> None:
    """记录各模板的校验结果：错误级→error 日志（请求时拒绝入缓）；告警仅提示。"""
    for template_id, report in mgr.validate_all().items():
        if not report.ok:
            problems = "; ".join(f"{f.code}:{f.message}" for f in report.errors())
            logger.error("模板 %s 未通过校验（请求时将拒绝入缓）: %s", template_id, problems)
        else:
            warnings = "; ".join(f"{f.code}:{f.message}" for f in report.warnings())
            logger.info(
                "模板 %s 校验通过%s", template_id, f"（告警: {warnings}）" if warnings else ""
            )


def _run_pipeline(mgr: TemplateManager, template_id: str, payload: dict) -> dict:
    """解析→校验→求值→渲染的统一编排（供 HTML/JSON 两路复用）。

    非法输入不抛错：相应节点置 UNKNOWN（§9.3），附校验 issues 供追溯。
    """
    cfg = mgr.get(template_id)
    model = mgr.get_model(template_id)

    validator = InputValidator.from_config(cfg)
    nv = validator.validate_node_values(payload.get("node_values") or {})
    basis, basis_issues = validator.validate_basis(payload.get("basis"))
    thresholds, thr_issues = validator.validate_thresholds(payload.get("thresholds"))

    context = build_parameter_context(cfg, {"basis": basis, "thresholds": thresholds})
    engine = RuleEngine(cfg.nodes, context)
    result = engine.evaluate(
        nv.values,
        template_id=cfg.template_id,
        well_id=payload.get("well_id"),
        rules_version=cfg.rules_meta.rules_version,
    )

    renderer = Renderer(cfg.style)
    out = renderer.render(
        mgr.flow_path(template_id),
        model,
        result,
        nodes=cfg.nodes,
        node_values=nv.values,
        title=f"油井压裂评价结果 · {payload.get('well_id') or cfg.template_id}",
    )

    all_issues = list(nv.issues) + list(basis_issues) + list(thr_issues)
    return {
        "summary": out["summary"],
        "html": out["html"],
        "ok": not all_issues,
        "issues": [
            {"location": i.location, "kind": i.kind, "reason": i.reason} for i in all_issues
        ],
    }


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
