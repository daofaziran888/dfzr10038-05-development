"""标注词典分版校验：面向历史标签值与新标签的词典校验明细。

词典按版本发布（词典版本字段）。同一个标签值在不同版本下状态不同：
历史版本里启用的旧值，在新版本里可能被停用并拆分成多个新值
（例如 missing 在 DICT-V2 停用，拆分为 unknown 与 omitted）。

审核员按校验明细（适用场景、标签层级、互斥组、拆分目标）检查样本；
新标签资格由适用范围与互斥条件判定；停用后的新录入不能继承旧值资格。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

MODULE = "label_dict"

ACTIVE_STATUSES = {"已发布", "已修订"}
DEPRECATED_STATUS = "已废弃"


def split_terms(raw: Any) -> list[str]:
    """把逗号/分号/顿号分隔的字段或列表统一成干净字符串列表。"""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;，；、/]", str(raw or "")) if part.strip()]


def version_rank(version: Any) -> int:
    """词典版本排序键：按版本号中的数字比较（DICT-V1 < DICT-V2）。"""
    match = re.search(r"(\d+)", str(version or ""))
    return int(match.group(1)) if match else 0


class DictionaryCatalog:
    """按词典版本读取校验明细，并对返修样本做资格裁定。"""

    def versions(self) -> list[str]:
        seen = {
            str(row.get("词典版本", ""))
            for row in store.rows(MODULE)
            if str(row.get("词典版本", "")).strip()
        }
        return sorted(seen, key=version_rank)

    def latest_version(self) -> str:
        versions = self.versions()
        return versions[-1] if versions else ""

    def rule_for(self, version: str, value: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("词典版本")) == str(version) and str(row.get("标签值")) == str(value):
                return row
        return None

    def validation_details(self, version: str) -> list[dict[str, Any]]:
        """词典校验明细：某版本下每个标签值的审核检查依据。

        历史标签值与新标签分版输出：旧值保留停用状态与拆分目标，
        新值给出适用场景、标签层级、互斥组，供审核员逐项检查样本。
        """
        details: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            if str(row.get("词典版本")) != str(version):
                continue
            status = str(row.get("status", ""))
            details.append({
                "词典编号": row.get("词典编号", ""),
                "词典版本": row.get("词典版本", ""),
                "标签名称": row.get("标签名称", ""),
                "标签值": row.get("标签值", ""),
                "标签层级": row.get("标签层级", ""),
                "互斥组": row.get("互斥组", ""),
                "适用场景": split_terms(row.get("适用场景")),
                "启用状态": "停用" if status == DEPRECATED_STATUS else (
                    "启用" if status in ACTIVE_STATUSES else status),
                "拆分目标": split_terms(row.get("拆分目标")),
            })
        return details

    def adjudicate(
        self,
        *,
        version: str,
        old_value: str,
        recorded_version: str,
        scenarios: list[str],
        current_labels: list[str],
    ) -> dict[str, Any]:
        """按校验明细裁定单个返修样本。

        返回 {结论, 新标签值, 说明}；结论 ∈ 核定通过 / 待核定 / 驳回。
        """
        rule = self.rule_for(version, old_value)
        if rule is None:
            return {"结论": "待核定", "新标签值": "",
                    "说明": f"旧值 {old_value} 未收录于词典版本 {version}，需人工核对"}
        status = str(rule.get("status", ""))

        if status == DEPRECATED_STATUS:
            # 停用后的新录入不能继承旧值的资格
            if version_rank(recorded_version) >= version_rank(version):
                return {"结论": "驳回", "新标签值": "",
                        "说明": f"标签值 {old_value} 在 {version} 已停用，"
                                f"停用后的新录入（{recorded_version}）不能继承旧值资格"}
            successors = split_terms(rule.get("拆分目标"))
            if not successors:
                return {"结论": "待核定", "新标签值": "",
                        "说明": f"旧值 {old_value} 已停用且未配置拆分目标"}
            evidence = [item for item in scenarios if str(item).strip()]
            if not evidence:
                return {"结论": "待核定", "新标签值": "",
                        "说明": "缺少场景证据，无法判定拆分新值"}
            eligible: list[tuple[str, dict[str, Any]]] = []
            for succ in successors:
                succ_rule = self.rule_for(version, succ)
                if succ_rule is None or str(succ_rule.get("status")) not in ACTIVE_STATUSES:
                    continue
                scope = split_terms(succ_rule.get("适用场景"))
                if set(evidence) & set(scope):
                    eligible.append((succ, succ_rule))
            if not eligible:
                return {"结论": "待核定", "新标签值": "",
                        "说明": "场景证据未覆盖任何拆分值的适用范围"}
            if len(eligible) > 1:
                return {"结论": "待核定", "新标签值": "",
                        "说明": "场景证据命中多个拆分值，需补充互斥判定"}
            new_value, new_rule = eligible[0]
            mutex_group = str(new_rule.get("互斥组", "")).strip()
            if mutex_group:
                for label in current_labels:
                    if label in (old_value, new_value):
                        continue
                    other = self.rule_for(version, label)
                    if other and str(other.get("互斥组", "")).strip() == mutex_group:
                        return {"结论": "待核定", "新标签值": "",
                                "说明": f"新值 {new_value} 与现有标签 {label} 同属互斥组 {mutex_group}"}
            return {"结论": "核定通过", "新标签值": new_value,
                    "说明": f"旧值 {old_value} 已停用，按场景证据拆分为 {new_value}"}

        if status in ACTIVE_STATUSES:
            return {"结论": "核定通过", "新标签值": old_value,
                    "说明": f"旧值 {old_value} 在 {version} 仍为有效标签，保留原值"}
        return {"结论": "待核定", "新标签值": "",
                "说明": f"旧值 {old_value} 在 {version} 状态为 {status}，不可核定"}

    def validate_new_entry(self, values: dict[str, Any]) -> str | None:
        """新录入词典项校验：已停用的标签值不能被新录入继承旧资格。"""
        value = str(values.get("标签值", "")).strip()
        if not value:
            return None
        version = str(values.get("词典版本", "")).strip() or self.latest_version()
        existing = self.rule_for(version, value)
        if existing and str(existing.get("status")) == DEPRECATED_STATUS:
            successors = split_terms(existing.get("拆分目标"))
            suffix = f"，已拆分为 {'、'.join(successors)}" if successors else ""
            return (f"标签值 {value} 在 {version} 已停用{suffix}，"
                    f"新录入不能继承旧值资格")
        return None


dict_catalog = DictionaryCatalog()
