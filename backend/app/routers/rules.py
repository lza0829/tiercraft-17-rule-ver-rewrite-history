from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services import rules

router = APIRouter(tags=["清洗规则包"])


class RuleItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    rule_code: str | None = Field(default=None, description="规则编号")
    rule_type: str = Field(..., description="必填空值 / 金额下限 / 品类白名单")
    field: str | None = Field(default=None, description="作用字段")
    config: dict | None = Field(default=None, description="规则配置")
    description: str | None = Field(default=None, description="规则说明")


class RulePackBody(BaseModel):
    version: str = Field(..., description="版本号，登记后不可修改")
    name: str | None = Field(default=None, description="规则包名称")
    description: str | None = Field(default=None, description="规则包说明")
    rules: list[RuleItem] = Field(..., description="规则清单")
    enabled: bool = Field(default=False, description="登记后是否立即启用")


class ActivateBody(BaseModel):
    version: str | None = Field(default=None, description="要启用的版本号")


@router.get("/rule-packs")
def list_rule_packs():
    return rules.list_rule_packs()


@router.get("/rule-packs/active")
def get_active_pack():
    pack = rules.get_active_pack()
    if not pack:
        raise HTTPException(status_code=404, detail="尚未启用任何清洗规则包")
    return pack


@router.get("/rule-packs/{pack_id}")
def get_rule_pack(pack_id: int):
    pack = rules.get_rule_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="规则包不存在")
    return pack


@router.post("/rule-packs", status_code=201)
def create_rule_pack(body: RulePackBody):
    payload = {
        "version": body.version,
        "name": body.name,
        "description": body.description,
        "rules": [r.model_dump(exclude_none=True) for r in body.rules],
    }
    try:
        pack = rules.create_rule_pack(payload)
        if body.enabled:
            pack = rules.activate_rule_pack(int(pack["id"]))
    except rules.RulePackConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except rules.RulePackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return pack


@router.post("/rule-packs/{pack_id}/activate")
def activate_rule_pack(pack_id: int):
    try:
        return rules.activate_rule_pack(pack_id)
    except rules.RulePackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rules.RulePackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rule-packs/activate")
def activate_by_version(body: ActivateBody):
    if not body.version:
        raise HTTPException(status_code=400, detail="请提供要启用的版本号")
    try:
        return rules.activate_by_version(body.version)
    except rules.RulePackNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except rules.RulePackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
