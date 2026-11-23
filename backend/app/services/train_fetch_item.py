"""领取明细领域服务：训练领取的逐样本交付记录，用于对账。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class TrainFetchItemService(BaseModuleService):
    MODULE = "train_fetch_item"
    ENTITY = "领取明细"
    OPERATION_TERM = "核对"
    LIST_FIELDS = ["领取单号", "返修批次", "词典版本", "样本编号", "标签值",
                   "领取人", "领取时间"]
    REQUIRED_FIELDS = ["领取单号", "返修批次", "样本编号"]
    STATUS_ORDER = ["已交付", "已核对"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []
