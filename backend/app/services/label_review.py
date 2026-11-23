"""标注审核领域服务：读取词典校验明细并确认返修批。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.rework_flow import CONFIRM_MODULE, rework_flow
from app.store import store


class LabelReviewService(BaseModuleService):
    MODULE = "label_review"
    ENTITY = "审核批次"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["批次编号", "批次类型", "标注任务", "词典版本", "交付规则", "审核人",
                   "样本数量", "通过数量", "待核定数量", "驳回数量", "一致率", "审核时间"]
    REQUIRED_FIELDS = ["批次编号", "标注任务", "审核人"]
    STATUS_ORDER = ["待审核", "审核中", "已通过", "返修中"]
    ACTION_RULES = {"提交审核": "审核中", "确认通过": "已通过", "发起返修": "返修中"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str):
        if action == "提交返修确认":
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            # 审核员只提交一次返修确认：确认单号按批次+轮次生成，重复动作幂等
            round_no = len([
                row for row in store.rows(CONFIRM_MODULE)
                if str(row.get("返修批次")) == str(entry.get("批次编号"))
            ]) + 1
            confirmation_no = f"{entry.get('批次编号')}-C{round_no}"
            _, message = rework_flow.confirm_rework(
                batch_no=str(entry.get("批次编号")),
                confirmation_no=confirmation_no,
                auditor=str(entry.get("审核人") or "审核员"),
            )
            return store.find(self.MODULE, entry_id), message
        return super().run_action(entry_id, action)
