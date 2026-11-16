from fastapi import APIRouter, HTTPException, Query

from app.services import pipeline, rules

router = APIRouter(tags=["分层"])


@router.get("/layers/counts")
def counts(
    domain_id: int | None = Query(default=None),
    rule_version: str | None = Query(default=None),
):
    return pipeline.layer_counts(domain_id, rule_version)


@router.get("/layers/versions")
def list_versions(
    layer: str = Query(default="detail", description="detail / summary / rejection"),
    domain_id: int | None = Query(default=None),
):
    try:
        return pipeline.layer_versions(layer, domain_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/layers/rejections")
def list_rejections(
    domain_id: int | None = Query(default=None),
    job_id: int | None = Query(default=None),
    rule_version: str | None = Query(default=None),
):
    return rules.list_rejections(domain_id, job_id, rule_version)


@router.get("/layers/{layer}")
def list_layer(
    layer: str,
    domain_id: int | None = Query(default=None),
    rule_version: str | None = Query(default=None),
):
    mapping = {"paste": "paste", "detail": "detail", "summary": "summary",
               "贴源": "paste", "明细": "detail", "汇总": "summary"}
    key = mapping.get(layer)
    if not key:
        raise HTTPException(status_code=400, detail="分层只能是贴源/明细/汇总")
    try:
        return pipeline.layer_rows(key, domain_id, rule_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
