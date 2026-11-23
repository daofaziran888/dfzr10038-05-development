"""训练领取领域服务：训练操作员按实际已放行的分批或整批结果领取样本。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.rework_flow import rework_flow
from app.store import store


class TrainFetchService(BaseModuleService):
    MODULE = "train_fetch"
    ENTITY = "领取任务"
    OPERATION_TERM = "领取"
    LIST_FIELDS = ["领取编号", "返修批次", "词典版本", "交付规则",
                   "可领取数量", "已领取数量", "最近领取时间"]
    REQUIRED_FIELDS = ["领取编号", "返修批次", "词典版本"]
    STATUS_ORDER = ["待放行", "待领取", "部分领取", "已领完"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str):
        if action == "领取样本":
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            _, message = rework_flow.collect(
                fetch_no=str(entry.get("领取编号")), operator="训练操作员")
            return store.find(self.MODULE, entry_id), message
        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
