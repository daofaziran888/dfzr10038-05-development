"""标注词典领域服务：分版校验明细、停用拆分与新录入资格拦截。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.dict_catalog import dict_catalog


class LabelDictService(BaseModuleService):
    MODULE = "label_dict"
    ENTITY = "标注项"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["词典编号", "词典版本", "标签名称", "标签值", "标签层级", "描述说明",
                   "互斥组", "适用场景", "拆分目标", "使用次数"]
    REQUIRED_FIELDS = ["词典编号", "标签名称", "标签值"]
    STATUS_ORDER = ["草稿", "已发布", "已修订", "已废弃"]
    ACTION_RULES = {"发布标签": "已发布", "修订标签": "已修订", "废弃标签": "已废弃"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]):
        # 停用后的新录入不能继承旧值的资格
        blocked = dict_catalog.validate_new_entry(values)
        if blocked:
            return None, [blocked]
        return super().create_entry(values)

    def validation_details(self, version: str | None = None) -> dict[str, Any]:
        """词典校验明细：面向历史标签值和新标签的分版校验。"""
        target = version or dict_catalog.latest_version()
        return {
            "词典版本": target,
            "可用版本": dict_catalog.versions(),
            "明细": dict_catalog.validation_details(target),
        }
