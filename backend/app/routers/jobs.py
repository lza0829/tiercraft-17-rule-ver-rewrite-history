from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import pipeline

router = APIRouter(tags=["分层任务"])


class JobBody(BaseModel):
    domain_id: int = Field(..., description="主题域编号")


@router.get("/jobs")
def list_jobs(domain_id: int | None = None):
    return pipeline.list_jobs(domain_id)


@router.get("/jobs/{job_id}/report")
def job_report(job_id: int):
    report = pipeline.job_report(job_id)
    if not report:
        raise HTTPException(status_code=404, detail="任务不存在")
    return report


@router.get("/jobs/{job_id}/verdict")
def job_verdict(job_id: int):
    """规则裁决证明（只读）：启用版本、输入/写入/拒收计数与样本键。"""
    verdict = pipeline.get_verdict(job_id)
    if not verdict:
        raise HTTPException(status_code=404, detail="该任务没有规则裁决证明")
    return verdict


@router.post("/jobs/paste-to-detail")
def paste_to_detail(body: JobBody):
    try:
        job = pipeline.run_paste_to_detail(body.domain_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job["step_label"] = "贴源到明细"
    return job


@router.post("/jobs/detail-to-summary")
def detail_to_summary(body: JobBody):
    try:
        job = pipeline.run_detail_to_summary(body.domain_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job["step_label"] = "明细到汇总"
    return job
