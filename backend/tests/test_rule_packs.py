from __future__ import annotations

import importlib
import json
import sys

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """每个用例独立 SQLite 与夹具目录。"""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "门店日销售-贴源.jsonl").write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {"业务日": "2026-11-10", "门店编码": "S01", "门店名称": "城东店",
                 "品类": "饮料", "金额": 120.5, "件数": 10},
                {"业务日": "2026-11-10", "门店编码": "S01", "门店名称": "城东店",
                 "品类": "零食", "金额": 86.0, "件数": 7},
                {"业务日": "2026-11-10", "门店编码": "S02", "门店名称": "城西店",
                 "品类": "饮料", "金额": 55.0, "件数": 4},
                {"业务日": "2026-11-11", "门店编码": "S01", "门店名称": "城东店",
                 "品类": "饮料", "金额": 99.0, "件数": 8},
                {"业务日": "2026-11-11", "门店编码": "S02", "门店名称": "城西店",
                 "品类": "零食", "金额": 40.0, "件数": 3},
                {"业务日": "2026-11-11", "门店编码": "S03", "门店名称": "",
                 "品类": "饮料", "金额": -5.0, "件数": 1},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FIXTURES_DIR", str(fixtures))

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    seed = importlib.import_module("app.seed")
    seed.init_db()

    from app.services import pipeline, rules

    return pipeline, rules


def _strict_pack(rules):
    return rules.create_rule_pack(
        {
            "version": "v2-严格",
            "name": "严格口径",
            "rules": [
                {"rule_code": "R001", "rule_type": "required",
                 "config": {"fields": ["store_name"]}},
                {"rule_code": "R002", "rule_type": "amount_min",
                 "config": {"min": 50}},
                {"rule_code": "R003", "rule_type": "category_whitelist",
                 "config": {"allowed": ["饮料"]}},
            ],
        }
    )


def test_seed_registers_single_active_pack(fresh_db):
    _, rules = fresh_db
    packs = rules.list_rule_packs()
    assert len(packs) == 1
    active = rules.get_active_pack()
    assert active is not None and active["enabled"]
    types = {r["rule_type"] for r in active["rules"]}
    assert {"required", "amount_min", "category_whitelist"} <= types


def test_create_pack_requires_all_three_rule_types(fresh_db):
    _, rules = fresh_db
    with pytest.raises(rules.RulePackError):
        rules.create_rule_pack(
            {
                "version": "v-bad",
                "rules": [
                    {"rule_code": "R001", "rule_type": "required",
                     "config": {"fields": ["store_name"]}},
                ],
            }
        )


def test_version_is_unique(fresh_db):
    _, rules = fresh_db
    payload = {
        "version": "v1",
        "rules": [
            {"rule_code": "R001", "rule_type": "required",
             "config": {"fields": ["store_name"]}},
            {"rule_code": "R002", "rule_type": "amount_min", "config": {"min": 0}},
            {"rule_code": "R003", "rule_type": "category_whitelist",
             "config": {"allowed": ["饮料"]}},
        ],
    }
    with pytest.raises(rules.RulePackConflict):
        rules.create_rule_pack(payload)


def test_only_one_enabled_pack_at_a_time(fresh_db):
    _, rules = fresh_db
    pack = _strict_pack(rules)
    assert rules.get_active_pack()["version"] == "v1"
    rules.activate_rule_pack(pack["id"])
    enabled = [p for p in rules.list_rule_packs() if p["enabled"]]
    assert len(enabled) == 1
    assert enabled[0]["version"] == "v2-严格"
    assert enabled[0]["activated_at"]


def test_rejected_rows_go_to_reject_list_not_detail(fresh_db):
    pipeline, rules = fresh_db
    pack = _strict_pack(rules)
    rules.activate_rule_pack(pack["id"])
    job = pipeline.run_paste_to_detail(1)

    assert job["rule_pack_version"] == "v2-严格"
    assert (job["in_count"], job["out_count"], job["drop_count"]) == (6, 3, 3)

    detail = pipeline.layer_rows("detail", 1, "v2-严格")
    assert len(detail) == 3
    assert all(r["rule_pack_version"] == "v2-严格" for r in detail)
    assert {r["paste_id"] for r in detail} == {1, 3, 4}

    rejects = rules.list_rejections(1, rule_version="v2-严格")
    assert len(rejects) == 3
    assert {r["rule_code"] for r in rejects} == {"R001", "R002", "R003"}
    assert all(r["paste_id"] for r in rejects)
    assert all(r["reason"] for r in rejects)
    # 拒收行没有进入明细
    assert not ({r["paste_id"] for r in rejects} & {r["paste_id"] for r in detail})


def test_switch_version_does_not_rewrite_history(fresh_db):
    pipeline, rules = fresh_db
    job_v1 = pipeline.run_paste_to_detail(1)
    assert job_v1["rule_pack_version"] == "v1"
    v1_rows = pipeline.layer_rows("detail", 1, "v1")
    assert len(v1_rows) == 5

    pack = _strict_pack(rules)
    rules.activate_rule_pack(pack["id"])
    job_v2 = pipeline.run_paste_to_detail(1)

    # 历史行原版本号不变，也没有被删除
    still_v1 = pipeline.layer_rows("detail", 1, "v1")
    assert [(r["id"], r["rule_pack_version"]) for r in still_v1] == [
        (r["id"], "v1") for r in v1_rows
    ]
    v2_rows = pipeline.layer_rows("detail", 1, "v2-严格")
    assert len(v2_rows) == 3
    assert job_v2["id"] != job_v1["id"]

    # 切回旧版本也不改写任何明细
    rules.activate_rule_pack(1)
    assert len(pipeline.layer_rows("detail", 1, "v1")) == 5
    assert len(pipeline.layer_rows("detail", 1, "v2-严格")) == 3


def test_job_report_matches_layers_and_active_version(fresh_db):
    pipeline, rules = fresh_db
    job = pipeline.run_paste_to_detail(1)
    pipeline.run_detail_to_summary(1)
    report = pipeline.job_report(job["id"])

    assert report["规则版本"] == "v1"
    assert report["当前启用版本"] == "v1"
    assert report["贴源行数"] == job["in_count"]
    assert report["明细行数"] == job["out_count"]
    assert report["拒收行数"] == job["drop_count"]
    assert report["一致"] is True

    rec = pipeline.run_reconcile(1)
    assert rec["detail"]["规则版本"] == "v1"
    assert rec["detail"]["贴源覆盖"] is True
    assert rec["detail"]["贴源行数"] == report["贴源行数"]
    assert rec["detail"]["明细行数"] == report["明细行数"]
    assert rec["detail"]["拒收行数"] == report["拒收行数"]


def test_rerun_same_version_supersedes_old_job(fresh_db):
    pipeline, _ = fresh_db
    first = pipeline.run_paste_to_detail(1)
    second = pipeline.run_paste_to_detail(1)
    assert pipeline.job_report(first["id"])["任务"]["superseded"] is True
    assert pipeline.job_report(second["id"])["任务"]["superseded"] is False
    assert pipeline.job_report(second["id"])["明细行数"] == second["out_count"]


def test_reconcile_requires_active_pack(fresh_db):
    pipeline, rules = fresh_db
    # 清空启用状态，模拟没有启用版本
    from app.db import connect

    conn = connect()
    try:
        conn.execute("UPDATE rule_packs SET enabled=0")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError):
        pipeline.run_paste_to_detail(1)
