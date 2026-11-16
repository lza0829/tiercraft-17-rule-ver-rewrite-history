from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import connect, rows_to_dicts

# 规则包必须至少覆盖这三类规则
REQUIRED_RULE_TYPES = ("required", "amount_min", "category_whitelist")

RULE_TYPE_LABEL = {
    "required": "必填校验",
    "amount_min": "金额下限",
    "category_whitelist": "品类白名单",
}

RULE_TYPE_ALIASES = {
    "required": "required",
    "required_field": "required",
    "not_null": "required",
    "必填": "required",
    "必填空值": "required",
    "amount_min": "amount_min",
    "min_amount": "amount_min",
    "minimum_amount": "amount_min",
    "金额下限": "amount_min",
    "category_whitelist": "category_whitelist",
    "category": "category_whitelist",
    "品类白名单": "category_whitelist",
}

FIELD_LABEL = {
    "biz_day": "业务日",
    "store_code": "门店编码",
    "store_name": "门店名称",
    "category": "品类",
    "amount": "金额",
    "qty": "件数",
}


class RulePackError(ValueError):
    """规则包参数或状态不合法。"""


class RulePackConflict(RulePackError):
    """版本号或启用状态冲突。"""


class RulePackNotFound(RulePackError):
    """规则包不存在。"""


def _field_label(field: str) -> str:
    return FIELD_LABEL.get(field, field)


def _normalize_rule_type(value: Any) -> str:
    key = str(value or "").strip()
    normalized = RULE_TYPE_ALIASES.get(key)
    if not normalized:
        raise RulePackError(
            f"未知规则类型「{key}」，可选：必填空值 / 金额下限 / 品类白名单"
        )
    return normalized


def _normalize_fields(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    out: list[str] = []
    for item in parts:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _normalize_rule(raw: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise RulePackError("规则必须是对象")
    rule_type = _normalize_rule_type(
        raw.get("rule_type") or raw.get("type") or raw.get("类型")
    )
    code = str(
        raw.get("rule_code") or raw.get("code") or raw.get("规则编号") or ""
    ).strip()
    if not code:
        code = f"R{index + 1:03d}"
    field = str(raw.get("field") or raw.get("字段") or "").strip()
    config = raw.get("config") or raw.get("params") or raw.get("配置") or {}
    if not isinstance(config, dict):
        raise RulePackError(f"规则 {code} 的配置必须是对象")

    if rule_type == "required":
        fields = _normalize_fields(
            config.get("fields")
            or config.get("required")
            or config.get("必填字段")
            or field
            or raw.get("fields")
        )
        if not fields:
            raise RulePackError(f"规则 {code}（必填空值）必须指定至少一个必填字段")
        config = {"fields": fields}
        field = fields[0]
        description = f"必填字段：{'、'.join(_field_label(f) for f in fields)}"
    elif rule_type == "amount_min":
        field = field or "amount"
        raw_min = (
            config.get("min")
            if "min" in config
            else config.get("min_amount", raw.get("min", raw.get("金额下限")))
        )
        try:
            minimum = float(raw_min)
        except (TypeError, ValueError) as exc:
            raise RulePackError(f"规则 {code}（金额下限）的 min 必须是数字") from exc
        config = {"min": minimum}
        description = f"金额不得小于 {minimum:g}"
    else:  # category_whitelist
        field = field or "category"
        allowed = _normalize_fields(
            config.get("allowed")
            or config.get("categories")
            or config.get("whitelist")
            or config.get("values")
            or config.get("品类白名单")
            or raw.get("allowed")
        )
        if not allowed:
            raise RulePackError(f"规则 {code}（品类白名单）必须配置至少一个品类")
        config = {"allowed": allowed}
        description = f"品类白名单：{'、'.join(allowed)}"

    description = str(
        raw.get("description") or raw.get("说明") or description
    ).strip()

    return {
        "rule_code": code,
        "rule_type": rule_type,
        "rule_type_label": RULE_TYPE_LABEL[rule_type],
        "field": field,
        "config": config,
        "description": description,
    }


def normalize_rules(rules: list[dict]) -> list[dict]:
    if not isinstance(rules, list) or not rules:
        raise RulePackError("规则包至少需要一条规则")
    normalized: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(rules):
        rule = _normalize_rule(raw, index)
        if rule["rule_code"] in seen:
            raise RulePackError(f"规则编号「{rule['rule_code']}」重复")
        seen.add(rule["rule_code"])
        normalized.append(rule)
    types = {r["rule_type"] for r in normalized}
    missing = [RULE_TYPE_LABEL[t] for t in REQUIRED_RULE_TYPES if t not in types]
    if missing:
        raise RulePackError(f"规则包缺少必需规则：{'、'.join(missing)}")
    return normalized


def _pack_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    rules = rows_to_dicts(
        conn.execute(
            "SELECT * FROM rule_pack_rules WHERE pack_id=? ORDER BY id",
            (int(row["id"]),),
        ).fetchall()
    )
    for rule in rules:
        try:
            rule["config"] = json.loads(rule.pop("config_json") or "{}")
        except json.JSONDecodeError:
            rule["config"] = {}
            rule.pop("config_json", None)
        rule["rule_type_label"] = RULE_TYPE_LABEL.get(
            rule["rule_type"], rule["rule_type"]
        )
    return {
        "id": int(row["id"]),
        "version": row["version"],
        "name": row["name"],
        "description": row["description"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "activated_at": row["activated_at"],
        "rules": rules,
        "rule_count": len(rules),
    }


def _fetch_pack(conn: sqlite3.Connection, pack_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM rule_packs WHERE id=?", (pack_id,)).fetchone()
    return _pack_from_row(conn, row) if row else None


def _fetch_active(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM rule_packs WHERE enabled=1 ORDER BY id LIMIT 1"
    ).fetchone()
    return _pack_from_row(conn, row) if row else None


def list_rule_packs() -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM rule_packs ORDER BY id DESC"
        ).fetchall()
        return [_pack_from_row(conn, row) for row in rows]
    finally:
        conn.close()


def get_rule_pack(pack_id: int) -> dict | None:
    conn = connect()
    try:
        return _fetch_pack(conn, pack_id)
    finally:
        conn.close()


def get_active_pack() -> dict | None:
    conn = connect()
    try:
        return _fetch_active(conn)
    finally:
        conn.close()


def get_active_pack_in(conn: sqlite3.Connection) -> dict | None:
    return _fetch_active(conn)


def create_rule_pack(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise RulePackError("请求体必须是对象")
    version = str(payload.get("version") or payload.get("版本") or "").strip()
    if not version:
        raise RulePackError("版本号不能为空")
    if len(version) > 40:
        raise RulePackError("版本号不能超过 40 个字符")
    name = str(
        payload.get("name") or payload.get("名称") or f"清洗规则包 {version}"
    ).strip()
    description = str(
        payload.get("description") or payload.get("说明") or ""
    ).strip()
    rules = normalize_rules(
        payload.get("rules") or payload.get("规则") or []
    )

    conn = connect()
    try:
        exists = conn.execute(
            "SELECT id FROM rule_packs WHERE version=?", (version,)
        ).fetchone()
        if exists:
            raise RulePackConflict(f"版本号「{version}」已登记")
        cur = conn.execute(
            """
            INSERT INTO rule_packs(version, name, description, enabled)
            VALUES (?, ?, ?, 0)
            """,
            (version, name, description),
        )
        pack_id = int(cur.lastrowid)
        for rule in rules:
            conn.execute(
                """
                INSERT INTO rule_pack_rules(
                  pack_id, rule_code, rule_type, field, config_json, description
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    rule["rule_code"],
                    rule["rule_type"],
                    rule["field"],
                    json.dumps(rule["config"], ensure_ascii=False),
                    rule["description"],
                ),
            )
        conn.commit()
        return _fetch_pack(conn, pack_id) or {}
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise RulePackConflict(f"版本号「{version}」已登记") from exc
    finally:
        conn.close()


def activate_rule_pack(pack_id: int) -> dict:
    """启用指定版本。

    只改动 rule_packs 的启用标记，不触碰任何历史明细行的版本号。
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM rule_packs WHERE id=?", (pack_id,)
        ).fetchone()
        if not row:
            raise RulePackNotFound("规则包不存在")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE rule_packs SET enabled=0 WHERE enabled=1")
        conn.execute(
            """
            UPDATE rule_packs
               SET enabled=1, activated_at=datetime('now')
             WHERE id=?
            """,
            (pack_id,),
        )
        conn.commit()
        return _fetch_pack(conn, pack_id) or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def activate_by_version(version: str) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id FROM rule_packs WHERE version=?", (str(version).strip(),)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise RulePackNotFound("规则包不存在")
    return activate_rule_pack(int(row["id"]))


def _required_fields(rule: dict) -> list[str]:
    config = rule.get("config") or {}
    return _normalize_fields(config.get("fields"))


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def evaluate_row(rule_pack: dict, row: dict | sqlite3.Row) -> tuple[dict | None, str]:
    """按规则顺序裁决一行，返回 (命中的规则, 原因)；通过返回 (None, "")。"""
    data = dict(row)
    for rule in rule_pack.get("rules") or []:
        rule_type = rule["rule_type"]
        if rule_type == "required":
            for field in _required_fields(rule):
                if _is_blank(data.get(field)):
                    return rule, f"「{_field_label(field)}」为必填，当前为空"
        elif rule_type == "amount_min":
            config = rule.get("config") or {}
            field = rule.get("field") or "amount"
            value = data.get(field)
            try:
                amount = float(value)
            except (TypeError, ValueError):
                return rule, f"「{_field_label(field)}」格式非法：{value!r}"
            minimum = float(config.get("min", 0))
            if amount < minimum:
                return rule, (
                    f"金额 {amount:g} 小于下限 {minimum:g}"
                )
        elif rule_type == "category_whitelist":
            config = rule.get("config") or {}
            field = rule.get("field") or "category"
            value = data.get(field)
            text = "" if value is None else str(value).strip()
            allowed = _normalize_fields(config.get("allowed"))
            if text not in allowed:
                return rule, (
                    f"品类「{text or '（空）'}」不在白名单：{'、'.join(allowed)}"
                )
    return None, ""


def list_rejections(
    domain_id: int | None = None,
    job_id: int | None = None,
    rule_version: str | None = None,
) -> list[dict]:
    sql = """
        SELECT r.*, p.biz_day, p.store_code, p.store_name, p.category,
               p.amount, p.qty
          FROM detail_rejections r
          LEFT JOIN layer_paste p ON p.id = r.paste_id
         WHERE 1=1
    """
    args: list[Any] = []
    if domain_id is not None:
        sql += " AND r.domain_id=?"
        args.append(domain_id)
    if job_id is not None:
        sql += " AND r.job_id=?"
        args.append(job_id)
    if rule_version:
        sql += " AND r.rule_version=?"
        args.append(rule_version)
    sql += " ORDER BY r.id DESC"
    conn = connect()
    try:
        out = rows_to_dicts(conn.execute(sql, tuple(args)).fetchall())
        for row in out:
            row["rule_type_label"] = RULE_TYPE_LABEL.get(
                row["rule_type"], row["rule_type"]
            )
            row["paste_ref"] = f"贴源#{row['paste_id']}"
            row.pop("raw_json", None)
        return out
    finally:
        conn.close()


def count_rejections(domain_id: int | None = None) -> int:
    conn = connect()
    try:
        if domain_id is None:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM detail_rejections"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM detail_rejections WHERE domain_id=?",
                (domain_id,),
            ).fetchone()
        return int(row["c"])
    finally:
        conn.close()


DEFAULT_PACK_VERSION = "v1"


def seed_default_pack(conn: sqlite3.Connection) -> None:
    """没有登记过任何规则包时，登记并启用一套默认规则。"""
    count = int(conn.execute("SELECT COUNT(*) AS c FROM rule_packs").fetchone()["c"])
    if count:
        return
    cur = conn.execute(
        """
        INSERT INTO rule_packs(version, name, description, enabled, activated_at)
        VALUES (?, ?, ?, 1, datetime('now'))
        """,
        (
            DEFAULT_PACK_VERSION,
            "默认清洗规则",
            "演示用基线：必填、金额下限、品类白名单",
        ),
    )
    pack_id = int(cur.lastrowid)
    default_rules = [
        {
            "rule_code": "R001",
            "rule_type": "required",
            "field": "store_name",
            "config_json": json.dumps({"fields": ["store_name"]}, ensure_ascii=False),
            "description": "必填字段：门店名称",
        },
        {
            "rule_code": "R002",
            "rule_type": "amount_min",
            "field": "amount",
            "config_json": json.dumps({"min": 0}, ensure_ascii=False),
            "description": "金额不得小于 0",
        },
        {
            "rule_code": "R003",
            "rule_type": "category_whitelist",
            "field": "category",
            "config_json": json.dumps(
                {"allowed": ["饮料", "零食"]}, ensure_ascii=False
            ),
            "description": "品类白名单：饮料、零食",
        },
    ]
    for rule in default_rules:
        conn.execute(
            """
            INSERT INTO rule_pack_rules(
              pack_id, rule_code, rule_type, field, config_json, description
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                rule["rule_code"],
                rule["rule_type"],
                rule["field"],
                rule["config_json"],
                rule["description"],
            ),
        )
