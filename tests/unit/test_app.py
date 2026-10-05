"""Flask 接口测试（Phase 1 · ISSUE-1.9 端到端）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import create_app

TEMPLATE_ID = "oil_fracturing_v1"
VIEWER = "https://viewer.diagrams.net/js/viewer-static.min.js"


@pytest.fixture()
def sample(templates_root: Path) -> dict:  # noqa: ANN001
    return json.loads((templates_root / TEMPLATE_ID / "sample_request.json").read_text(encoding="utf-8"))


@pytest.fixture()
def sample_true(templates_root: Path) -> dict:  # noqa: ANN001
    return json.loads(
        (templates_root / TEMPLATE_ID / "sample_request_true.json").read_text(encoding="utf-8")
    )


@pytest.fixture()
def app_obj(templates_root: Path):  # noqa: ANN001
    app = create_app(templates_root)
    app.config["TESTING"] = True
    return app


def test_health_ok(client) -> None:  # noqa: ANN001
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_list_templates(client) -> None:  # noqa: ANN001
    data = client.get("/api/v1/templates").get_json()
    assert data["count"] >= 1
    ids = {t["id"] for t in data["templates"]}
    assert "oil_fracturing_v1" in ids


# ── 默认输出 HTML（内嵌 draw.io viewer）──────────────────
def test_evaluate_returns_html(client, sample) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate", json=sample)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    body = resp.get_data(as_text=True)
    assert VIEWER in body                                   # viewer 脚本
    assert "#FF0000" in body                                # C001 FALSE→红（来自 style）
    assert "x12" not in body                                 # P006 阈值占位符已换为数值（§6.6 改版）


# ── result=json 返回结构化三态 ────────────────────────────
def test_evaluate_json_via_query(client, sample) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate?result=json", json=sample)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["template_id"] == TEMPLATE_ID
    assert data["well_id"] == "W001"
    assert data["root_state"] == "FALSE"                     # 最终结论 C001
    assert data["node_states"]["C001"] == "FALSE"
    assert data["node_states"]["P003"] == "FALSE"            # 9.0>9.0 不成立
    assert data["colors"]["C013"] == "#00B050"               # 三色来自 style
    assert "R06" not in data["colors"]                        # 关系节点不着色
    assert "validation" in data


def test_evaluate_json_via_accept_header(client, sample) -> None:  # noqa: ANN001
    resp = client.post(
        "/api/v1/evaluate", json=sample, headers={"Accept": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["root_state"] == "FALSE"


# ── 全部达标：根结论 C001=TRUE（JSON 分支）────────────────
def test_evaluate_json_root_true(client, sample_true) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate?result=json", json=sample_true)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["well_id"] == "W-TRUE"
    assert data["root_state"] == "TRUE"
    assert data["node_states"]["C001"] == "TRUE"
    assert data["node_states"]["P003"] == "TRUE"             # 邻井产油翻真打通关键路径
    assert data["node_states"]["P008"] == "TRUE"
    assert data["colors"]["C001"] == "#00B050"               # 根结论→绿
    assert not [c for c in data["colors"] if c.startswith("R")]  # 关系节点仍不入色
    assert data["validation"]["ok"] is True


# ── 全部达标：HTML 分支含绿色与阈值替换───────────────
def test_evaluate_true_returns_html(client, sample_true) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate", json=sample_true)
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    body = resp.get_data(as_text=True)
    assert VIEWER in body
    assert "#00B050" in body                                 # 全绿结论
    assert "#FF0000" not in body                             # 无红（未出现 FALSE 着色）
    assert "x12" not in body and "10 t" in body              # P006 占位符→请求阈值 10（实依多重转义只断裸片段）


# ── 非法/缺失输入：不抛错，相关节点置 UNKNOWN（§9.3）───────
def test_evaluate_empty_inputs_all_unknown(client) -> None:  # noqa: ANN001
    resp = client.post(
        "/api/v1/evaluate?result=json",
        json={"template": TEMPLATE_ID, "well_id": "W009", "node_values": {}},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["root_state"] == "UNKNOWN"
    assert all(v == "UNKNOWN" for v in data["node_states"].values())


# ── 错误码：缺 template/缺 node_values→400；未知模板→404 ────
def test_evaluate_missing_template(client) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"


def test_evaluate_missing_node_values_400(client) -> None:  # noqa: ANN001
    # 入参契约（2026-10-05 拍板）：必填 template + node_values，字段必须提供（可空对象）
    resp = client.post("/api/v1/evaluate", json={"template": TEMPLATE_ID})
    assert resp.status_code == 400
    assert "node_values" in resp.get_json()["message"]


def test_evaluate_non_object_node_values_400(client) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate", json={"template": TEMPLATE_ID, "node_values": [1, 2]})
    assert resp.status_code == 400


def test_evaluate_unknown_template(client) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/evaluate", json={"template": "nope", "node_values": {}})
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "template_not_found"


# ── TemplateManager 解析缓存复用（§10.4）──────────────────
def test_model_cache_reused(app_obj) -> None:  # noqa: ANN001
    mgr = app_obj.config["MANAGER"]
    m1 = mgr.get_model(TEMPLATE_ID)
    m2 = mgr.get_model(TEMPLATE_ID)
    assert m1 is m2                                          # 同一实例，避免重复解析
    assert len(m1.code_map) == 39


# ── 演示入口：首页 / 与 /demo/<模板>[?sample=] ─────────────
def test_index_lists_demo_links(client) -> None:  # noqa: ANN001
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    body = resp.get_data(as_text=True)
    assert f"/demo/{TEMPLATE_ID}" in body                     # 标准样例链接
    for suffix in ("_true", "_missing", "_invalid", "_minimal"):
        assert f"?sample={suffix}" in body                    # 多样例自动枚举


@pytest.mark.parametrize("suffix", ["", "_true", "_missing", "_invalid", "_minimal"])
def test_demo_samples_render_html(client, suffix) -> None:  # noqa: ANN001
    resp = client.get(f"/demo/{TEMPLATE_ID}" + (f"?sample={suffix}" if suffix else ""))
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    body = resp.get_data(as_text=True)
    assert VIEWER in body                                    # 内嵌 draw.io viewer
    assert "x12" not in body                                 # 阈值占位符已换为数值（§6.6 改版）


def test_demo_unknown_template(client) -> None:  # noqa: ANN001
    resp = client.get("/demo/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "template_not_found"


def test_demo_unknown_sample_lists_available(client) -> None:  # noqa: ANN001
    resp = client.get(f"/demo/{TEMPLATE_ID}?sample=_nope")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"] == "sample_not_found"
    assert "sample_request_true.json" in data["available"]    # 404 附可用清单


def test_demo_bad_suffix_rejected(client) -> None:  # noqa: ANN001
    resp = client.get(f"/demo/{TEMPLATE_ID}?sample=../etc")
    assert resp.status_code == 400                            # 正则白名单拦截路径穿越
    assert resp.get_json()["error"] == "invalid_sample"
