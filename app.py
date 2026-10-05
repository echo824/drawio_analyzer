"""Flask 入口（Phase 0 骨架）。

职责：
  - 应用工厂 + /health 健康检查
  - 启动时通过 TemplateManager 加载/结构校验/缓存解析 templates/ 下的所有模板
  - /api/v1/evaluate：解析→校验→求值→渲染 端到端（默认 HTML，可 result=json 返回结构化三态）
  - / 与 /demo/<模板>：浏览器可直接访问的演示入口（用模板自带 sample_request 跑全链路）

运行：
    flask --app app run  或  python app.py
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from flask import Flask, Response, jsonify, request

from config_loader import ConfigError, TemplateNotFoundError
from core.expr import build_parameter_context
from core.inspector import DEFAULT_CODE_PATTERN, inspect_file
from core.renderer import Renderer
from core.rule_engine import RuleEngine
from core.template_manager import TemplateManager
from core.validator import InputValidator

TEMPLATES_ROOT = os.environ.get("TEMPLATES_ROOT", "templates")

# 演示样例后缀 → 中文标签（模板目录里的 sample_request*.json）
SAMPLE_LABELS = {
    "": "标准样例（黄金基准，根结论 FALSE）",
    "_true": "全部达标（14 个 P 全真 → C001=TRUE）",
    "_missing": "参数缺失（部分省略 → 相关节点 UNKNOWN）",
    "_invalid": "非法输入（越界/非数值/非法布尔 → 不抛错判 UNKNOWN）",
    "_minimal": "极简空输入（全部 UNKNOWN）",
}

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

    # ── 模板检视（只读，无需注册）：任意 .drawio 解析+结构体检 ──
    @app.post("/api/v1/templates/inspect")
    def inspect_template():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        raw_path = payload.get("path")
        if not raw_path:
            return jsonify({"error": "invalid_request", "message": "缺少 'path' 字段"}), 400
        p = Path(raw_path)
        if p.suffix.lower() != ".drawio":
            return jsonify({"error": "invalid_file", "detail": "仅支持 .drawio 文件"}), 400
        if not p.is_file():
            return jsonify({"error": "file_not_found", "detail": str(p)}), 404
        report = inspect_file(p, payload.get("pattern") or DEFAULT_CODE_PATTERN)
        return jsonify(report.to_dict()), (200 if report.parse_error is None else 422)

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

    # ── 首页：服务说明 + 演示入口（浏览器 GET 可达，免 404）────
    @app.get("/")
    def index():  # noqa: ANN202
        mgr = app.config["MANAGER"]
        rows: list[str] = []
        for cfg in mgr.registry.values():
            for p in sorted(cfg.path.glob("sample_request*.json")):
                suffix = p.stem[len("sample_request"):]
                label = SAMPLE_LABELS.get(suffix, f"样例{suffix}")
                query = f"?sample={suffix}" if suffix else ""
                rows.append(
                    f'<li><a href="/demo/{cfg.template_id}{query}">'
                    f"{cfg.template_id} · {label}</a></li>"
                )
        links = "".join(rows) or "<li>无可用样例</li>"
        return (
            '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
            "<title>油井压裂评价微服务</title></head>"
            "<body style=\"font-family:'Microsoft YaHei',Arial,sans-serif;padding:32px\">"
            "<h1>油井压裂评价微服务</h1><h2>接口</h2><ul>"
            "<li><code>GET /health</code> 健康检查</li>"
            "<li><code>GET /api/v1/templates</code> 模板清单</li>"
            "<li><code>POST /api/v1/templates/inspect</code> 检视任意 .drawio（只读，无需注册）</li>"
            "<li><code>POST /api/v1/evaluate</code> 评价（默认 HTML，可 ?result=json）</li>"
            "</ul><h2>演示（模板自带多样例，均直出全链路评价图）</h2>"
            f"<ul>{links}</ul></body></html>"
        ), 200

    # ── 演示：按模板样例 GET 直出评价 HTML（供浏览器/验收）──
    # ?sample= 接后缀（_true/_missing/_invalid/_minimal），缺省为标准样例
    @app.get("/demo/<template_id>")
    def demo(template_id: str):  # noqa: ANN202
        mgr = app.config["MANAGER"]
        if template_id not in mgr.registry:
            raise TemplateNotFoundError(template_id)
        suffix = (request.args.get("sample") or "").strip()
        if suffix and not re.fullmatch(r"_[A-Za-z0-9]+", suffix):
            return jsonify({"error": "invalid_sample", "detail": suffix}), 400
        cfg = mgr.get(template_id)
        sample = cfg.path / f"sample_request{suffix}.json"
        if not sample.exists():
            available = sorted(p.name for p in cfg.path.glob("sample_request*.json"))
            return jsonify({"error": "sample_not_found", "detail": str(sample),
                            "available": available}), 404
        payload = json.loads(sample.read_text(encoding="utf-8"))
        data = _run_pipeline(mgr, template_id, payload)
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
    thresholds, thr_issues = validator.validate_thresholds(payload.get("thresholds"))

    context = build_parameter_context(cfg, {"thresholds": thresholds})
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
        symbols=context.symbols,                       # §6.6 改版：描述中的阈值符号→具体数值
        title=f"油井压裂评价结果 · {payload.get('well_id') or cfg.template_id}",
    )

    all_issues = list(nv.issues) + list(thr_issues)
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
