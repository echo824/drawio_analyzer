"""热部署 `TemplateManager.hot_swap_flow`（F3·Phase 3，2026-10-05 拍板）。

范围拍板：仅 flow.drawio 原位替换 + 落盘持久；多版本/结果追溯搁置。
测试全部在 tmp_path 沙箱副本上进行，不触碰真实 templates/ 目录。
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from app import create_app
from core.template_manager import FLOW_FILENAME, TemplateManager, TemplateValidationError

TEMPLATE_ID = "oil_fracturing_v1"


@pytest.fixture()
def sandbox(templates_root: Path, tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    shutil.copytree(templates_root, root)
    return root


@pytest.fixture()
def mgr(sandbox: Path) -> TemplateManager:
    return TemplateManager(sandbox)


def _flow_bytes(sandbox: Path) -> bytes:
    return (sandbox / TEMPLATE_ID / FLOW_FILENAME).read_bytes()


# ── 成功路径：落盘、备份、缓存刷新、体检摘要 ──────────────
def test_swap_success_persists_and_refreshes_cache(sandbox: Path, mgr: TemplateManager) -> None:
    old = mgr.get_model(TEMPLATE_ID)
    payload = _flow_bytes(sandbox)

    result = mgr.hot_swap_flow(TEMPLATE_ID, payload)

    assert result["template_id"] == TEMPLATE_ID
    assert (result["node_count"], result["edge_count"], result["business_codes"]) == (39, 44, 39)
    assert result["backup"].endswith("flow.drawio.bak")
    assert {w["code"] for w in result["warnings"]} >= {"V10"}     # 悬空边告警放行（G3 口径）
    # 落盘：替换后文件内容与 payload 一致；备份保留旧文件
    flow = sandbox / TEMPLATE_ID / FLOW_FILENAME
    assert flow.read_bytes() == payload
    assert flow.with_name(flow.name + ".bak").read_bytes() == payload
    # 缓存刷新：get_model 返回新解析实例（非旧对象）
    assert mgr.get_model(TEMPLATE_ID) is not old
    # 临时文件已被原子替换移走/清理
    assert not (sandbox / TEMPLATE_ID / f".{FLOW_FILENAME}.upload.tmp").exists()


# ── 解析失败：拒绝、磁盘不动、无备份、缓存保留旧模型 ──────
def test_swap_bad_xml_rejected_disk_untouched(mgr: TemplateManager, sandbox: Path) -> None:
    good = mgr.get_model(TEMPLATE_ID)
    before = _flow_bytes(sandbox)

    with pytest.raises(TemplateValidationError):
        mgr.hot_swap_flow(TEMPLATE_ID, b"<mxfile><broken")

    assert (sandbox / TEMPLATE_ID / FLOW_FILENAME).read_bytes() == before   # 原文件未动
    assert not (sandbox / TEMPLATE_ID / f"{FLOW_FILENAME}.bak").exists()    # 未产生备份
    assert mgr.get_model(TEMPLATE_ID) is good                               # 缓存仍是旧模型


# ── 校验错误级：业务码集合与 rules.yaml 不一致（V20/V21）──
def test_swap_code_mismatch_rejected(mgr: TemplateManager, sandbox: Path) -> None:
    good = mgr.get_model(TEMPLATE_ID)
    renamed = _flow_bytes(sandbox).replace(b"P012", b"P099")   # 图上码集偏离规则契约

    with pytest.raises(TemplateValidationError) as exc:
        mgr.hot_swap_flow(TEMPLATE_ID, renamed)

    assert any("V20" in p or "V21" in p for p in exc.value.problems)
    assert (sandbox / TEMPLATE_ID / FLOW_FILENAME).read_bytes() != renamed  # 磁盘未写入坏图
    assert mgr.get_model(TEMPLATE_ID) is good


# ── 空上传：直接拒绝 ──────────────────────────────────────
def test_swap_empty_payload_rejected(mgr: TemplateManager) -> None:
    with pytest.raises(TemplateValidationError):
        mgr.hot_swap_flow(TEMPLATE_ID, b"")


# 旧文件缺失（如被误删）：热部署可恢复落盘，backup=None
def test_swap_write_without_existing_file(mgr: TemplateManager, sandbox: Path) -> None:
    flow = sandbox / TEMPLATE_ID / FLOW_FILENAME
    payload = flow.read_bytes()
    flow.unlink()                                  # 注册表已建，不触发启动结构校验

    result = mgr.hot_swap_flow(TEMPLATE_ID, payload)

    assert result["backup"] is None
    assert flow.read_bytes() == payload


# ── HTTP 面：成功 / 缺 file / 后缀错 / 坏文件 422 / 模板 404 ──
@pytest.fixture()
def sclient(sandbox: Path):  # noqa: ANN201
    app = create_app(sandbox)
    app.config["TESTING"] = True
    return app.test_client()


def test_http_swap_ok(sclient, sandbox: Path) -> None:
    payload = _flow_bytes(sandbox)
    resp = sclient.post(
        f"/api/v1/templates/{TEMPLATE_ID}/flow",
        data={"file": (io.BytesIO(payload), "flow.drawio")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "swapped" and body["node_count"] == 39


def test_http_swap_validation_failure_422(sclient, sandbox: Path) -> None:
    resp = sclient.post(
        f"/api/v1/templates/{TEMPLATE_ID}/flow",
        data={"file": (io.BytesIO(b"<mxfile/>"), "flow.drawio")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "validation_failed"


def test_http_swap_rejects_missing_or_wrong_file(sclient) -> None:
    assert sclient.post(f"/api/v1/templates/{TEMPLATE_ID}/flow", data={}).status_code == 400
    resp = sclient.post(
        f"/api/v1/templates/{TEMPLATE_ID}/flow",
        data={"file": (io.BytesIO(b"x"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_http_swap_unknown_template_404(sclient) -> None:
    resp = sclient.post(
        "/api/v1/templates/nope_v9/flow",
        data={"file": (io.BytesIO(b"x"), "flow.drawio")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404
