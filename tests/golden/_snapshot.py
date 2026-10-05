"""黄金快照计算工具（ISSUE-1.10）。

对任意模板根目录跑「校验→求值→着色/阈值替换」，产出一足以业务码为主键的、
与随机 mxCell.id 无关的稳定快照（三态 / 结果色 / 替换后描述）。供回归与
配置解耦测试复用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config_loader import load_templates
from core.drawio_parser import DrawioParser
from core.expr import build_parameter_context
from core.renderer import Renderer
from core.rule_engine import RuleEngine
from core.validator import InputValidator

HERE = Path(__file__).resolve().parent


def golden_file(template_id: str) -> Path:
    return HERE / f"{template_id}.sample.json"


def _load(root: Path, template_id: str):  # noqa: ANN202
    cfg = load_templates(root)[template_id]
    model = DrawioParser(cfg.node_code_pattern).parse(cfg.path / "flow.drawio")
    payload = json.loads((root / template_id / "sample_request.json").read_text(encoding="utf-8"))
    return cfg, model, payload


def run_bundle(root: Path, template_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """对任意 payload 跑完整链路，返回业务码主键的结果包（含 issues/ok）。

    供回归快照与多示例（缺失/非法）测试、演示生成复用。"""
    cfg = load_templates(root)[template_id]
    model = DrawioParser(cfg.node_code_pattern).parse(cfg.path / "flow.drawio")

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
    summary = renderer.summary_dict(model, result)
    id_to_code = {v: k for k, v in model.code_map.items()}
    patches = renderer.substitute(model, context.symbols)       # cell_id → 新 label（§6.6 改版）
    annotations = {id_to_code[cid]: lab for cid, lab in patches.items() if cid in id_to_code}
    all_issues = list(nv.issues) + list(thr_issues)

    return {
        "template_id": summary["template_id"],
        "well_id": summary["well_id"],
        "root_state": summary["root_state"],
        "node_states": dict(sorted(summary["node_states"].items())),
        "colors": dict(sorted(summary["colors"].items())),
        "annotations": dict(sorted(annotations.items())),
        "ok": not all_issues,
        "issues": [{"location": i.location, "kind": i.kind, "reason": i.reason} for i in all_issues],
    }


def snapshot_for(root: Path, template_id: str) -> dict[str, Any]:
    """黄金样例的稳定性快照（不含 issues，仅业务码三态/结果色/替换后描述）。"""
    _, _, payload = _load(root, template_id)
    b = run_bundle(root, template_id, payload)
    return {
        "template_id": b["template_id"],
        "well_id": b["well_id"],
        "root_state": b["root_state"],
        "node_states": b["node_states"],
        "colors": b["colors"],
        "annotations": b["annotations"],
    }


def write_golden(snapshot: dict[str, Any], template_id: str) -> Path:
    path = golden_file(template_id)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


__all__ = ["snapshot_for", "run_bundle", "write_golden", "golden_file", "HERE"]
