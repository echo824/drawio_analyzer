"""模板脚手架生成器 `core.scaffolder` 测试。

用真实 v1 图（39 码 + 复杂拓扑）驱动生成，验证：结构完整、YAML 语法可加载、
rules↔parameters 符号自洽（不留 V23 隐患）、聚合新规、拒绝覆盖与非法 id。
全部输出到 tmp_path，不写真实 templates/ 目录。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from core.scaffolder import main, scaffold

TEMPLATE_ID = "my_new_biz_v1"


@pytest.fixture()
def generated(templates_root: Path, tmp_path: Path):  # noqa: ANN201
    out = scaffold(templates_root / "oil_fracturing_v1" / "flow.drawio",
                   TEMPLATE_ID, out_root=tmp_path, name="新业务")
    return out, tmp_path / TEMPLATE_ID


def test_generates_full_set(generated) -> None:  # noqa: ANN001
    out, d = generated
    for f in ("flow.drawio", "template.yaml", "rules.yaml", "parameters.yaml",
              "style.yaml", "sample_request.json"):
        assert (d / f).is_file(), f
    assert (len(out.p_codes), len(out.r_codes), len(out.c_codes)) == (14, 14, 11)
    assert out.children_pairs > 0                       # 拓扑推导出了引用
    assert "TODO" in out.summary()


def test_yaml_files_are_loadable(generated) -> None:  # noqa: ANN001
    _, d = generated
    rules = yaml.safe_load((d / "rules.yaml").read_text(encoding="utf-8"))
    params = yaml.safe_load((d / "parameters.yaml").read_text(encoding="utf-8"))
    meta = yaml.safe_load((d / "template.yaml").read_text(encoding="utf-8"))
    assert rules["meta"]["template"] == TEMPLATE_ID
    assert meta["template"]["id"] == TEMPLATE_ID
    # 39 个业务码全覆盖，R=AND / C=OR 新规自动定档
    for code in rules["P001"], rules["R01"], rules["C001"]:
        assert code["type"] in ("predicate", "rule", "conclusion")
    assert all(rules[c]["aggregate"] == "AND" for c in out_r(rules))
    assert all(rules[c]["aggregate"] == "OR" for c in out_c(rules))
    assert len(params["quantities"]) == 14 and len(params["thresholds"]) == 14


def out_r(rules: dict) -> list[str]:
    return [k for k in rules if k.startswith("R")]


def out_c(rules: dict) -> list[str]:
    return [k for k in rules if k.startswith("C")]


def test_symbols_self_consistent(generated) -> None:  # noqa: ANN001
    """rules 引用的阈值符号全部在 parameters.thresholds 有定义（不留 V23 坑）。"""
    _, d = generated
    rules_text = (d / "rules.yaml").read_text(encoding="utf-8")
    params = yaml.safe_load((d / "parameters.yaml").read_text(encoding="utf-8"))
    used = set(re.findall(r"threshold:\s*(\S+)", rules_text))
    assert used and used <= set(params["thresholds"])


def test_sample_has_required_node_values(generated) -> None:  # noqa: ANN001
    _, d = generated
    sample = json.loads((d / "sample_request.json").read_text(encoding="utf-8"))
    assert sample["template"] == TEMPLATE_ID
    assert "node_values" in sample                      # 入参契约：字段必供
    assert len(sample["node_values"]) == 14


def test_refuses_overwrite_and_bad_id(generated, templates_root: Path, tmp_path: Path) -> None:  # noqa: ANN001
    _, d = generated
    with pytest.raises(FileExistsError):
        scaffold(templates_root / "oil_fracturing_v1" / "flow.drawio", TEMPLATE_ID, out_root=tmp_path)
    with pytest.raises(ValueError):
        scaffold(d / "flow.drawio", "非法 id 空格", out_root=tmp_path)


def test_cli_main(tmp_path: Path, templates_root: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main([str(templates_root / "oil_fracturing_v1" / "flow.drawio"),
               "--id", "cli_biz", "--out-root", str(tmp_path)])
    assert rc == 0
    assert "草稿已生成" in capsys.readouterr().out
    assert main([str(templates_root / "oil_fracturing_v1" / "flow.drawio"),
                 "--id", "cli_biz", "--out-root", str(tmp_path)]) == 1   # 重复→失败退出码
