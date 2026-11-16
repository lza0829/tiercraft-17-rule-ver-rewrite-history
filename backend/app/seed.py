from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.db import FIXTURES_DIR, connect
from app.services.rules import DEFAULT_PACK_VERSION, seed_default_pack


SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  biz_key TEXT NOT NULL,
  fixture_file TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rule_packs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  activated_at TEXT
);

CREATE TABLE IF NOT EXISTS rule_pack_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_id INTEGER NOT NULL,
  rule_code TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  field TEXT NOT NULL DEFAULT '',
  config_json TEXT NOT NULL DEFAULT '{}',
  description TEXT NOT NULL DEFAULT '',
  UNIQUE(pack_id, rule_code),
  FOREIGN KEY(pack_id) REFERENCES rule_packs(id)
);

CREATE TABLE IF NOT EXISTS layer_paste (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  biz_day TEXT NOT NULL,
  store_code TEXT NOT NULL,
  store_name TEXT NOT NULL,
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  qty INTEGER NOT NULL,
  raw_json TEXT NOT NULL,
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);

CREATE TABLE IF NOT EXISTS layer_detail (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  paste_id INTEGER,
  job_id INTEGER,
  biz_day TEXT NOT NULL,
  store_code TEXT NOT NULL,
  store_name TEXT NOT NULL,
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  qty INTEGER NOT NULL,
  clean_flag TEXT NOT NULL,
  rule_pack_version TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);

CREATE TABLE IF NOT EXISTS layer_summary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  biz_day TEXT NOT NULL,
  store_code TEXT NOT NULL,
  amount_sum REAL NOT NULL,
  qty_sum INTEGER NOT NULL,
  line_count INTEGER NOT NULL,
  rule_pack_version TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(domain_id, biz_day, store_code, rule_pack_version),
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);

CREATE TABLE IF NOT EXISTS detail_rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  job_id INTEGER,
  paste_id INTEGER NOT NULL,
  rule_code TEXT NOT NULL,
  rule_type TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  reason TEXT NOT NULL,
  raw_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(domain_id) REFERENCES domains(id),
  FOREIGN KEY(paste_id) REFERENCES layer_paste(id)
);

CREATE TABLE IF NOT EXISTS layer_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  step TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '',
  in_count INTEGER NOT NULL DEFAULT 0,
  out_count INTEGER NOT NULL DEFAULT 0,
  drop_count INTEGER NOT NULL DEFAULT 0,
  rule_pack_id INTEGER,
  rule_pack_version TEXT,
  superseded_by_job_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);

-- 规则裁决证明：贴源到明细任务落库时同事务写入，之后只读不改
CREATE TABLE IF NOT EXISTS rule_verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL UNIQUE,
  domain_id INTEGER NOT NULL,
  rule_pack_version TEXT NOT NULL,
  in_count INTEGER NOT NULL,
  out_count INTEGER NOT NULL,
  reject_count INTEGER NOT NULL,
  sample_keys_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(job_id) REFERENCES layer_jobs(id),
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);

CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id INTEGER NOT NULL,
  paste_count INTEGER NOT NULL,
  detail_count INTEGER NOT NULL,
  summary_line_sum INTEGER NOT NULL,
  key_mismatch INTEGER NOT NULL,
  amount_drift REAL NOT NULL,
  status TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  rule_pack_version TEXT,
  job_id INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(domain_id) REFERENCES domains(id)
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    """兼容早期库：补列，并重建带版本维度的汇总表。"""
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if "layer_detail" in existing:
        _add_column(conn, "layer_detail", "paste_id", "INTEGER")
        _add_column(conn, "layer_detail", "job_id", "INTEGER")
        _add_column(conn, "layer_detail", "created_at", "TEXT")
        _add_column(
            conn, "layer_detail", "rule_pack_version", "TEXT NOT NULL DEFAULT '未登记'"
        )

    if "layer_jobs" in existing:
        _add_column(conn, "layer_jobs", "rule_pack_id", "INTEGER")
        _add_column(conn, "layer_jobs", "rule_pack_version", "TEXT")
        _add_column(conn, "layer_jobs", "superseded_by_job_id", "INTEGER")

    if "reconcile_reports" in existing:
        _add_column(conn, "reconcile_reports", "rule_pack_version", "TEXT")
        _add_column(conn, "reconcile_reports", "job_id", "INTEGER")

    if "layer_summary" in existing:
        cols = _columns(conn, "layer_summary")
        if "rule_pack_version" not in cols:
            conn.execute("ALTER TABLE layer_summary RENAME TO layer_summary_legacy")
            conn.executescript(
                """
                CREATE TABLE layer_summary (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  domain_id INTEGER NOT NULL,
                  biz_day TEXT NOT NULL,
                  store_code TEXT NOT NULL,
                  amount_sum REAL NOT NULL,
                  qty_sum INTEGER NOT NULL,
                  line_count INTEGER NOT NULL,
                  rule_pack_version TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT (datetime('now')),
                  UNIQUE(domain_id, biz_day, store_code, rule_pack_version),
                  FOREIGN KEY(domain_id) REFERENCES domains(id)
                );
                """
            )
            conn.execute(
                """
                INSERT INTO layer_summary(
                  domain_id, biz_day, store_code, amount_sum, qty_sum,
                  line_count, rule_pack_version
                )
                SELECT domain_id, biz_day, store_code, amount_sum, qty_sum,
                       line_count, '未登记'
                  FROM layer_summary_legacy
                """
            )
            conn.execute("DROP TABLE layer_summary_legacy")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # 索引可能因旧库补列后再建
        conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_detail_paste_version
              ON layer_detail(paste_id, rule_pack_version);
            CREATE UNIQUE INDEX IF NOT EXISTS ux_reject_paste_version
              ON detail_rejections(paste_id, rule_version);
            CREATE UNIQUE INDEX IF NOT EXISTS ux_rule_pack_enabled
              ON rule_packs(enabled) WHERE enabled=1;
            """
        )
        row = conn.execute("SELECT COUNT(*) AS c FROM domains").fetchone()
        if int(row["c"]) == 0:
            _seed_domain(conn)
        seed_default_pack(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_domain(conn) -> None:
    fixture = "门店日销售-贴源.jsonl"
    cur = conn.execute(
        """
        INSERT INTO domains(name, biz_key, fixture_file, description)
        VALUES (?, ?, ?, ?)
        """,
        (
            "门店日销售",
            "业务日+门店编码",
            fixture,
            "演示贴源装载、明细清洗与按门店日汇总",
        ),
    )
    domain_id = int(cur.lastrowid)
    path = FIXTURES_DIR / fixture
    if not path.exists():
        # 仓库内相对路径兜底
        alt = Path(__file__).resolve().parents[2] / "fixtures" / fixture
        path = alt
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        conn.execute(
            """
            INSERT INTO layer_paste(
              domain_id, biz_day, store_code, store_name, category, amount, qty, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                domain_id,
                obj.get("业务日") or "",
                obj.get("门店编码") or "",
                obj.get("门店名称") or "",
                obj.get("品类") or "",
                float(obj.get("金额") or 0),
                int(obj.get("件数") or 0),
                json.dumps(obj, ensure_ascii=False),
            ),
        )
