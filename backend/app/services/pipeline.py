from __future__ import annotations

import json
import sqlite3

from app.db import connect, rows_to_dicts
from app.services import rules


STEP_LABEL = {
    "paste_to_detail": "贴源到明细",
    "detail_to_summary": "明细到汇总",
}


def list_domains() -> list[dict]:
    conn = connect()
    try:
        return rows_to_dicts(conn.execute("SELECT * FROM domains ORDER BY id").fetchall())
    finally:
        conn.close()


def get_domain(domain_id: int) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM domains WHERE id=?", (domain_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def layer_rows(
    table: str, domain_id: int | None = None, rule_version: str | None = None
) -> list[dict]:
    allowed = {
        "paste": "layer_paste",
        "detail": "layer_detail",
        "summary": "layer_summary",
        "rejection": "detail_rejections",
    }
    if table not in allowed:
        raise ValueError("未知分层")
    if table == "rejection":
        if domain_id is not None and rule_version:
            return rules.list_rejections(domain_id, None, rule_version)
        return rules.list_rejections(domain_id)
    sql = f"SELECT * FROM {allowed[table]}"
    clauses: list[str] = []
    args: list = []
    if domain_id is not None:
        clauses.append("domain_id=?")
        args.append(domain_id)
    if rule_version and table in ("detail", "summary"):
        clauses.append("rule_pack_version=?")
        args.append(rule_version)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    conn = connect()
    try:
        rows = rows_to_dicts(conn.execute(sql, tuple(args)).fetchall())
        if table == "detail":
            for row in rows:
                row["paste_ref"] = (
                    f"贴源#{row['paste_id']}" if row.get("paste_id") else "—"
                )
        return rows
    finally:
        conn.close()


def layer_versions(layer: str, domain_id: int | None = None) -> list[dict]:
    """列出某层出现过的规则版本及行数，供界面切换查看历史版本。"""
    table, column = {
        "detail": ("layer_detail", "rule_pack_version"),
        "summary": ("layer_summary", "rule_pack_version"),
        "rejection": ("detail_rejections", "rule_version"),
    }.get(layer) or (None, None)
    if not table:
        raise ValueError("分层只能是明细/汇总/拒收")
    where = " WHERE domain_id=?" if domain_id is not None else ""
    args = (domain_id,) if domain_id is not None else ()
    conn = connect()
    try:
        active = rules.get_active_pack()
        active_version = active["version"] if active else None
        rows = conn.execute(
            f"""
            SELECT {column} AS rule_version, COUNT(*) AS row_count, MAX(id) AS last_id
              FROM {table}{where}
             GROUP BY {column}
             ORDER BY last_id DESC
            """,
            args,
        ).fetchall()
        return [
            {
                "rule_version": r["rule_version"],
                "row_count": int(r["row_count"]),
                "active": r["rule_version"] == active_version,
                "last_id": int(r["last_id"]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def layer_counts(domain_id: int | None = None, rule_version: str | None = None) -> dict:
    conn = connect()
    try:
        where = " WHERE domain_id=?" if domain_id is not None else ""
        args: list = [domain_id] if domain_id is not None else []
        paste = conn.execute(f"SELECT COUNT(*) AS c FROM layer_paste{where}", args).fetchone()["c"]

        detail_where = where
        detail_args = list(args)
        if rule_version:
            detail_where += (" AND " if where else " WHERE ") + "rule_pack_version=?"
            detail_args.append(rule_version)
        detail = conn.execute(
            f"SELECT COUNT(*) AS c FROM layer_detail{detail_where}", detail_args
        ).fetchone()["c"]

        summary_where = where
        summary_args = list(args)
        if rule_version:
            summary_where += (" AND " if where else " WHERE ") + "rule_pack_version=?"
            summary_args.append(rule_version)
        summary = conn.execute(
            f"SELECT COUNT(*) AS c FROM layer_summary{summary_where}", summary_args
        ).fetchone()["c"]

        reject_where = where
        reject_args = list(args)
        if rule_version:
            reject_where += (" AND " if where else " WHERE ") + "rule_version=?"
            reject_args.append(rule_version)
        rejected = conn.execute(
            f"SELECT COUNT(*) AS c FROM detail_rejections{reject_where}", reject_args
        ).fetchone()["c"]

        return {
            "贴源层": int(paste),
            "明细层": int(detail),
            "汇总层": int(summary),
            "拒收清单": int(rejected),
        }
    finally:
        conn.close()


def list_jobs(domain_id: int | None = None) -> list[dict]:
    conn = connect()
    try:
        if domain_id is None:
            rows = conn.execute("SELECT * FROM layer_jobs ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM layer_jobs WHERE domain_id=? ORDER BY id DESC", (domain_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["step_label"] = STEP_LABEL.get(d["step"], d["step"])
            d["superseded"] = bool(d.get("superseded_by_job_id"))
            if d["superseded"]:
                d["status_label"] = f"{d['status']}（已被任务 #{d['superseded_by_job_id']} 替代）"
            else:
                d["status_label"] = d["status"]
            out.append(d)
        return out
    finally:
        conn.close()


def _job_dict(conn: sqlite3.Connection, job_id: int) -> dict:
    row = conn.execute("SELECT * FROM layer_jobs WHERE id=?", (job_id,)).fetchone()
    d = dict(row)
    d["step_label"] = STEP_LABEL.get(d["step"], d["step"])
    return d


def run_paste_to_detail(domain_id: int) -> dict:
    domain = get_domain(domain_id)
    if not domain:
        raise ValueError("主题域不存在")
    pack = rules.get_active_pack()
    if not pack:
        raise ValueError("尚未启用清洗规则包，请先在「清洗规则包」登记并启用一个版本")
    version = pack["version"]

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 只重建当前启用版本自己的产物；历史版本明细与拒收记录保持不动
        conn.execute(
            "DELETE FROM layer_detail WHERE domain_id=? AND rule_pack_version=?",
            (domain_id, version),
        )
        conn.execute(
            "DELETE FROM detail_rejections WHERE domain_id=? AND rule_version=?",
            (domain_id, version),
        )
        # 明细重建后旧汇总已失去依据，只清当前版本的汇总，历史版本产物保持原样
        conn.execute(
            "DELETE FROM layer_summary WHERE domain_id=? AND rule_pack_version=?",
            (domain_id, version),
        )
        # 同一版本重跑会替换该版本产物；旧任务标记为已被替代，便于报告追溯
        previous_jobs = conn.execute(
            """
            SELECT id FROM layer_jobs
             WHERE domain_id=? AND rule_pack_version=?
               AND superseded_by_job_id IS NULL
               AND (step='paste_to_detail' OR step='detail_to_summary')
            """,
            (domain_id, version),
        ).fetchall()

        paste = conn.execute(
            "SELECT * FROM layer_paste WHERE domain_id=? ORDER BY id", (domain_id,)
        ).fetchall()
        job_cur = conn.execute(
            """
            INSERT INTO layer_jobs(
              domain_id, step, status, message, in_count, out_count, drop_count,
              rule_pack_id, rule_pack_version
            ) VALUES (?, 'paste_to_detail', '运行中', '', 0, 0, 0, ?, ?)
            """,
            (domain_id, int(pack["id"]), version),
        )
        job_id = int(job_cur.lastrowid)

        out = drop = 0
        written_keys: list[str] = []
        rejected_keys: list[str] = []
        for row in paste:
            biz_key = f"{row['biz_day']}|{row['store_code']}"
            hit, reason = rules.evaluate_row(pack, row)
            if hit is not None:
                conn.execute(
                    """
                    INSERT INTO detail_rejections(
                      domain_id, job_id, paste_id, rule_code, rule_type,
                      rule_version, reason, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        domain_id,
                        job_id,
                        int(row["id"]),
                        hit["rule_code"],
                        hit["rule_type"],
                        version,
                        reason,
                        row["raw_json"],
                    ),
                )
                drop += 1
                if len(rejected_keys) < 5:
                    rejected_keys.append(biz_key)
                continue
            conn.execute(
                """
                INSERT INTO layer_detail(
                  domain_id, paste_id, job_id, biz_day, store_code, store_name,
                  category, amount, qty, clean_flag, rule_pack_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    domain_id,
                    int(row["id"]),
                    job_id,
                    row["biz_day"],
                    row["store_code"],
                    (row["store_name"] or "").strip(),
                    row["category"],
                    float(row["amount"]),
                    int(row["qty"]),
                    "已清洗",
                    version,
                ),
            )
            out += 1
            if len(written_keys) < 5:
                written_keys.append(biz_key)

        conn.execute(
            """
            UPDATE layer_jobs
               SET status='成功', message=?, in_count=?, out_count=?, drop_count=?
             WHERE id=?
            """,
            (
                f"按规则包「{version}」裁决：写入明细 {out} 行，拒收 {drop} 行",
                len(paste),
                out,
                drop,
                job_id,
            ),
        )
        # 裁决证明与明细、拒收同一事务落库，之后只读不改
        conn.execute(
            """
            INSERT INTO rule_verdicts(
              job_id, domain_id, rule_pack_version,
              in_count, out_count, reject_count, sample_keys_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                domain_id,
                version,
                len(paste),
                out,
                drop,
                json.dumps(
                    {"写入样本键": written_keys, "拒收样本键": rejected_keys},
                    ensure_ascii=False,
                ),
            ),
        )
        for old in previous_jobs:
            conn.execute(
                "UPDATE layer_jobs SET superseded_by_job_id=? WHERE id=?",
                (job_id, int(old["id"])),
            )
        conn.commit()
        return _job_dict(conn, job_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_detail_to_summary(domain_id: int) -> dict:
    domain = get_domain(domain_id)
    if not domain:
        raise ValueError("主题域不存在")
    pack = rules.get_active_pack()
    if not pack:
        raise ValueError("尚未启用清洗规则包，请先在「清洗规则包」登记并启用一个版本")
    version = pack["version"]

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM layer_summary WHERE domain_id=? AND rule_pack_version=?",
            (domain_id, version),
        )
        detail = conn.execute(
            """
            SELECT * FROM layer_detail
             WHERE domain_id=? AND rule_pack_version=?
             ORDER BY id
            """,
            (domain_id, version),
        ).fetchall()
        previous_jobs = conn.execute(
            """
            SELECT id FROM layer_jobs
             WHERE domain_id=? AND step='detail_to_summary' AND rule_pack_version=?
               AND superseded_by_job_id IS NULL
            """,
            (domain_id, version),
        ).fetchall()
        buckets: dict[tuple, dict] = {}
        for row in detail:
            key = (row["biz_day"], row["store_code"])
            b = buckets.setdefault(
                key,
                {"biz_day": row["biz_day"], "store_code": row["store_code"], "amount": 0.0, "qty": 0, "n": 0},
            )
            b["amount"] += float(row["amount"])
            b["qty"] += int(row["qty"])
            b["n"] += 1
        for b in buckets.values():
            conn.execute(
                """
                INSERT INTO layer_summary(
                  domain_id, biz_day, store_code, amount_sum, qty_sum,
                  line_count, rule_pack_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    domain_id,
                    b["biz_day"],
                    b["store_code"],
                    b["amount"],
                    b["qty"],
                    b["n"],
                    version,
                ),
            )
        cur = conn.execute(
            """
            INSERT INTO layer_jobs(
              domain_id, step, status, message, in_count, out_count, drop_count,
              rule_pack_id, rule_pack_version
            ) VALUES (?, 'detail_to_summary', '成功', ?, ?, ?, 0, ?, ?)
            """,
            (
                domain_id,
                f"按版本「{version}」的明细汇总，业务日+门店编码分组",
                len(detail),
                len(buckets),
                int(pack["id"]),
                version,
            ),
        )
        conn.commit()
        new_job_id = int(cur.lastrowid)
        for old in previous_jobs:
            conn.execute(
                "UPDATE layer_jobs SET superseded_by_job_id=? WHERE id=?",
                (new_job_id, int(old["id"])),
            )
        conn.commit()
        return _job_dict(conn, new_job_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_reconcile(domain_id: int) -> dict:
    domain = get_domain(domain_id)
    if not domain:
        raise ValueError("主题域不存在")
    pack = rules.get_active_pack()
    if not pack:
        raise ValueError("尚未启用清洗规则包，请先在「清洗规则包」登记并启用一个版本")
    version = pack["version"]

    conn = connect()
    try:
        # 找出当前版本最近一次有效的贴源到明细任务，报告挂到这次任务上
        last_job = conn.execute(
            """
            SELECT id FROM layer_jobs
             WHERE domain_id=? AND step='paste_to_detail' AND rule_pack_version=?
               AND superseded_by_job_id IS NULL
             ORDER BY id DESC LIMIT 1
            """,
            (domain_id, version),
        ).fetchone()
        detail_job_id = int(last_job["id"]) if last_job else None
        paste_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM layer_paste WHERE domain_id=?", (domain_id,)
            ).fetchone()["c"]
        )
        detail_count = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c FROM layer_detail
                 WHERE domain_id=? AND rule_pack_version=?
                """,
                (domain_id, version),
            ).fetchone()["c"]
        )
        reject_count = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c FROM detail_rejections
                 WHERE domain_id=? AND rule_version=?
                """,
                (domain_id, version),
            ).fetchone()["c"]
        )
        summary = conn.execute(
            """
            SELECT COALESCE(SUM(line_count),0) AS s, COALESCE(SUM(amount_sum),0) AS a
              FROM layer_summary
             WHERE domain_id=? AND rule_pack_version=?
            """,
            (domain_id, version),
        ).fetchone()
        summary_line_sum = int(summary["s"])
        summary_amount = float(summary["a"])
        detail_amount = float(
            conn.execute(
                """
                SELECT COALESCE(SUM(amount),0) AS a FROM layer_detail
                 WHERE domain_id=? AND rule_pack_version=?
                """,
                (domain_id, version),
            ).fetchone()["a"]
        )
        key_mismatch = 1 if summary_line_sum != detail_count else 0
        amount_drift = round(summary_amount - detail_amount, 4)
        coverage_ok = detail_count + reject_count == paste_count
        status = (
            "一致"
            if key_mismatch == 0 and abs(amount_drift) < 0.0001 and coverage_ok
            else "存在差异"
        )
        detail = {
            "贴源行数": paste_count,
            "明细行数": detail_count,
            "拒收行数": reject_count,
            "贴源覆盖": coverage_ok,
            "汇总行数合计": summary_line_sum,
            "明细金额合计": detail_amount,
            "汇总金额合计": summary_amount,
            "键对齐": key_mismatch == 0,
            "金额漂移": amount_drift,
            "规则版本": version,
        }
        cur = conn.execute(
            """
            INSERT INTO reconcile_reports(
              domain_id, paste_count, detail_count, summary_line_sum,
              key_mismatch, amount_drift, status, detail_json, rule_pack_version, job_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain_id,
                paste_count,
                detail_count,
                summary_line_sum,
                key_mismatch,
                amount_drift,
                status,
                json.dumps(detail, ensure_ascii=False),
                version,
                detail_job_id,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM reconcile_reports WHERE id=?", (int(cur.lastrowid),)).fetchone()
        d = dict(row)
        d["detail"] = json.loads(d.pop("detail_json"))
        return d
    finally:
        conn.close()


def list_reconcile(domain_id: int | None = None) -> list[dict]:
    conn = connect()
    try:
        if domain_id is None:
            rows = conn.execute("SELECT * FROM reconcile_reports ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reconcile_reports WHERE domain_id=? ORDER BY id DESC",
                (domain_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            out.append(d)
        return out
    finally:
        conn.close()


def _verdict_dict(row: sqlite3.Row) -> dict:
    samples = json.loads(row["sample_keys_json"] or "{}")
    return {
        "任务编号": int(row["job_id"]),
        "主题域编号": int(row["domain_id"]),
        "启用版本": row["rule_pack_version"],
        "输入行数": int(row["in_count"]),
        "写入行数": int(row["out_count"]),
        "拒收行数": int(row["reject_count"]),
        "写入样本键": samples.get("写入样本键") or [],
        "拒收样本键": samples.get("拒收样本键") or [],
        "登记时间": row["created_at"],
    }


def get_verdict(job_id: int) -> dict | None:
    """按任务 id 读取规则裁决证明（只读）；非裁决任务或早期任务返回 None。"""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM rule_verdicts WHERE job_id=?", (job_id,)
        ).fetchone()
        return _verdict_dict(row) if row else None
    finally:
        conn.close()


def job_report(job_id: int) -> dict | None:
    """任务报告：输入/写入/拒收必须与明细、拒收清单、启用版本逐项对得上。"""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM layer_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = dict(row)
        job["step_label"] = STEP_LABEL.get(job["step"], job["step"])
        job["superseded"] = bool(job.get("superseded_by_job_id"))
        job["status_label"] = (
            f"{job['status']}（已被任务 #{job['superseded_by_job_id']} 替代）"
            if job["superseded"]
            else job["status"]
        )

        version = job.get("rule_pack_version")
        active = rules.get_active_pack()
        verdict_row = conn.execute(
            "SELECT * FROM rule_verdicts WHERE job_id=?", (job_id,)
        ).fetchone()
        report: dict = {
            "任务": job,
            "规则版本": version,
            "当前启用版本": active["version"] if active else None,
            "版本仍启用": bool(active and version == active["version"]),
            # 与 /api/jobs/{id}/verdict 同源，均出自 rule_verdicts
            "裁决证明": _verdict_dict(verdict_row) if verdict_row else None,
        }

        paste_total = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM layer_paste WHERE domain_id=?",
                (job["domain_id"],),
            ).fetchone()["c"]
        )
        report["贴源行数"] = paste_total

        if job["step"] == "paste_to_detail":
            detail_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM layer_detail
                     WHERE domain_id=? AND rule_pack_version=? AND job_id=?
                    """,
                    (job["domain_id"], version, job_id),
                ).fetchone()["c"]
            )
            reject_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM detail_rejections
                     WHERE domain_id=? AND rule_version=? AND job_id=?
                    """,
                    (job["domain_id"], version, job_id),
                ).fetchone()["c"]
            )
            report["明细行数"] = detail_count
            report["拒收行数"] = reject_count
            report["明细与写入一致"] = detail_count == int(job["out_count"])
            report["拒收与剔除一致"] = reject_count == int(job["drop_count"])
            report["输入等于写入加拒收"] = int(job["in_count"]) == int(
                job["out_count"]
            ) + int(job["drop_count"])
        if job["step"] == "detail_to_summary":
            detail_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM layer_detail
                     WHERE domain_id=? AND rule_pack_version=?
                    """,
                    (job["domain_id"], version),
                ).fetchone()["c"]
            )
            summary_lines = int(
                conn.execute(
                    """
                    SELECT COALESCE(SUM(line_count),0) AS s FROM layer_summary
                     WHERE domain_id=? AND rule_pack_version=?
                    """,
                    (job["domain_id"], version),
                ).fetchone()["s"]
            )
            report["明细行数"] = detail_count
            report["汇总行数合计"] = summary_lines
            report["明细与写入一致"] = detail_count == int(job["in_count"])
            report["汇总与写入一致"] = summary_lines == int(job["out_count"])

        # 被重跑替代的旧任务，其产物已被新任务重建，不能再拿当前层去核对
        if job["superseded"]:
            report["一致"] = None
            report["说明"] = (
                f"该任务已被任务 #{job['superseded_by_job_id']} 替代，"
                "当前层数据由新任务产出，历史明细不再保留核对基准。"
            )
            return report

        report["一致"] = all(
            bool(v) for k, v in report.items() if k.endswith("一致")
        )
        return report
    finally:
        conn.close()


def dashboard() -> dict:
    domains = list_domains()
    active = rules.get_active_pack()
    version = active["version"] if active else None
    counts = layer_counts(rule_version=version)
    jobs = list_jobs()[:5]
    reports = list_reconcile()[:5]
    return {
        "主题域数": len(domains),
        "分层计数": counts,
        "启用规则包": (
            {
                "版本": active["version"],
                "名称": active["name"],
                "规则数": active["rule_count"],
            }
            if active
            else None
        ),
        "统计版本": version,
        "最近任务": [
            {
                "编号": j["id"],
                "步骤": STEP_LABEL.get(j["step"], j["step"]),
                "状态": j["status"],
                "规则版本": j["rule_pack_version"],
                "写入": j["out_count"],
                "拒收": j["drop_count"],
            }
            for j in jobs
        ],
        "最近对账": [
            {
                "编号": r["id"],
                "状态": r["status"],
                "规则版本": r["rule_pack_version"],
                "明细行数": r["detail_count"],
                "汇总行数合计": r["summary_line_sum"],
            }
            for r in reports
        ],
        "主题域": [{"编号": d["id"], "名称": d["name"], "业务键": d["biz_key"]} for d in domains],
    }
