"""返修确认与训练领取的专用接口。

标注审核与训练领取两处入口关联同一返修批次和词典版本：
- 审核员读取词典校验明细、按批次提交一次返修确认；
- 标注员提交样本更正（同份更正只生效一次）；
- 训练操作员按实际已放行的分批或整批结果领取样本；
- 对账与历史批审核来源回查。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.dict_catalog import dict_catalog
from app.services.rework_flow import rework_flow

router = APIRouter(prefix="/api", tags=["返修与领取"])


class ConfirmPayload(BaseModel):
    确认单号: str
    审核人: str = "审核员"


class CorrectionPayload(BaseModel):
    更正单号: str
    场景证据: list[str] = Field(default_factory=list)


class CollectPayload(BaseModel):
    领取人: str = "训练操作员"


@router.get("/label_dict/validation-details")
def get_validation_details(version: str | None = Query(default=None)) -> dict[str, Any]:
    """词典校验明细：按版本输出，供审核员按适用场景、标签层级、互斥组检查样本。"""
    target = version or dict_catalog.latest_version()
    if not target:
        raise HTTPException(status_code=404, detail="词典尚无已发布版本")
    return {
        "词典版本": target,
        "可用版本": dict_catalog.versions(),
        "明细": dict_catalog.validation_details(target),
    }


@router.get("/label_review/{batch_no}/rework-detail")
def get_rework_detail(batch_no: str) -> dict[str, Any]:
    """返修批明细：批次、样本、词典校验明细与对账结果。"""
    detail = rework_flow.rework_detail(batch_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"返修批次 {batch_no} 不存在或已归档")
    return detail


@router.get("/label_review/{batch_no}/audit-source")
def get_audit_source(batch_no: str) -> dict[str, Any]:
    """审核来源回查：历史批的词典版本、标签与通过数独立可查。"""
    source = rework_flow.audit_source(batch_no)
    if source is None:
        raise HTTPException(status_code=404, detail=f"审核批次 {batch_no} 不存在或已归档")
    return source


@router.post("/label_review/{batch_no}/rework-confirm", response_model=ActionResult)
def confirm_rework(batch_no: str, payload: ConfirmPayload) -> ActionResult:
    """提交返修确认：审核员只提交一次，同一确认单号重复提交不重复生效。"""
    result, message = rework_flow.confirm_rework(
        batch_no=batch_no,
        confirmation_no=payload.确认单号,
        auditor=payload.审核人,
    )
    return ActionResult(ok=result is not None, message=message, entry=result)


@router.post("/rework_sample/{sample_no}/corrections", response_model=ActionResult)
def submit_correction(sample_no: str, payload: CorrectionPayload) -> ActionResult:
    """提交样本更正：同一份更正重复提交只生效一次。"""
    result, message = rework_flow.submit_correction(
        correction_no=payload.更正单号,
        sample_no=sample_no,
        scenarios=payload.场景证据,
    )
    return ActionResult(ok=result is not None, message=message, entry=result)


@router.post("/train_fetch/{fetch_no}/collect", response_model=ActionResult)
def collect(fetch_no: str, payload: CollectPayload) -> ActionResult:
    """训练领取：按返修批交付规则领取实际已放行的样本。"""
    result, message = rework_flow.collect(fetch_no=fetch_no, operator=payload.领取人)
    return ActionResult(ok=result is not None, message=message, entry=result)


@router.get("/train_fetch/{fetch_no}/consistency")
def get_consistency(fetch_no: str) -> dict[str, Any]:
    """对账：审核通过数与训练领取数核对，两处入口同一返修批次和词典版本。"""
    fetch = rework_flow.fetch_task(fetch_no)
    if fetch is None:
        raise HTTPException(status_code=404, detail=f"领取任务 {fetch_no} 不存在或已归档")
    result = rework_flow.consistency(str(fetch.get("返修批次")))
    if result is None:
        raise HTTPException(status_code=404, detail=f"返修批次 {fetch.get('返修批次')} 不存在或已归档")
    return result
