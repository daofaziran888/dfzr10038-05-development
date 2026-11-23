"""返修样本领域服务：返修批内样本的旧值、证据与核定状态。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class ReworkSampleService(BaseModuleService):
    MODULE = "rework_sample"
    ENTITY = "返修样本"
    OPERATION_TERM = "核定"
    LIST_FIELDS = ["样本编号", "返修批次", "旧标签值", "旧词典版本", "新标签值",
                   "场景证据", "核定说明"]
    REQUIRED_FIELDS = ["样本编号", "返修批次", "旧标签值"]
    STATUS_ORDER = ["待审核", "待核定", "待复核", "已核定", "已放行", "已领取", "已驳回"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []
