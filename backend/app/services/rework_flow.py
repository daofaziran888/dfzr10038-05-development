"""返修批确认与训练领取的领域流程。

标注审核入口读取词典校验明细并确认返修批；训练领取入口按返修批的
交付规则（分批交付/整批交付）领取实际已放行的样本。两处入口关联同一
返修批次和词典版本，保证审核通过数与训练领取数对得上。

关键约束：
- 审核员对同一确认单号只提交一次返修确认，重复提交不重复生效；
- 同一份更正重复提交只生效一次；
- 尚待核定的样本绝不能进入训练；
- 已核定的样本在等待期间按返修批的交付规则处理（整批交付需等批）；
- 已领取的样本不重复交付；
- 旧标签原值及使用的词典版本保留在样本与历史批上，规则更新后仍可回查。
"""
from __future__ import annotations

import datetime
from typing import Any

from app.services.dict_catalog import dict_catalog, split_terms
from app.store import store

REVIEW_MODULE = "label_review"
SAMPLE_MODULE = "rework_sample"
FETCH_MODULE = "train_fetch"
ITEM_MODULE = "train_fetch_item"
CONFIRM_MODULE = "rework_confirmation"
CORRECTION_MODULE = "rework_correction"

SAMPLE_UNRESOLVED = {"待审核", "待复核", "待核定"}
SAMPLE_APPROVED = {"已核定", "已放行", "已领取"}
PARTIAL = "分批交付"
WHOLE = "整批交付"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _next_id(module: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(module)), default=0) + 1


class ReworkFlowService:
    """返修批确认、更正、放行、领取与对账的领域服务。"""

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def review_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(REVIEW_MODULE):
            if str(row.get("批次编号")) == str(batch_no):
                return row
        return None

    def batch_samples(self, batch_no: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(SAMPLE_MODULE)
            if str(row.get("返修批次")) == str(batch_no)
        ]

    def fetch_task(self, fetch_no: str) -> dict[str, Any] | None:
        for row in store.rows(FETCH_MODULE):
            if str(row.get("领取编号")) == str(fetch_no):
                return row
        return None

    def fetch_task_for_batch(self, batch_no: str) -> dict[str, Any] | None:
        for row in store.rows(FETCH_MODULE):
            if str(row.get("返修批次")) == str(batch_no):
                return row
        return None

    def find_sample(self, sample_no: str) -> dict[str, Any] | None:
        for row in store.rows(SAMPLE_MODULE):
            if str(row.get("样本编号")) == str(sample_no):
                return row
        return None

    # ------------------------------------------------------------------
    # 返修确认：审核员只提交一次，确认单号幂等
    # ------------------------------------------------------------------
    def confirm_rework(
        self, *, batch_no: str, confirmation_no: str, auditor: str
    ) -> tuple[dict[str, Any] | None, str]:
        batch = self.review_batch(batch_no)
        if batch is None:
            return None, f"返修批次 {batch_no} 不存在或已归档"
        if str(batch.get("批次类型")) != "返修":
            return None, f"批次 {batch_no} 不是返修批，不能提交返修确认"
        if str(batch.get("status")) == "已通过":
            return None, f"返修批 {batch_no} 已完成，不能重复提交返修确认"

        # 同一确认单号只生效一次（审核员只提交一次返修确认）
        for row in store.rows(CONFIRM_MODULE):
            if str(row.get("确认单号")) == str(confirmation_no):
                return {"duplicated": True, "确认单号": confirmation_no,
                        "返修批次": batch_no}, (
                    f"返修确认 {confirmation_no} 已提交过，不重复生效")

        version = str(batch.get("词典版本", ""))
        verdicts: list[dict[str, Any]] = []
        for sample in self.batch_samples(batch_no):
            if str(sample.get("status")) not in SAMPLE_UNRESOLVED:
                continue
            verdict = dict_catalog.adjudicate(
                version=version,
                old_value=str(sample.get("旧标签值", "")),
                recorded_version=str(sample.get("录入版本") or sample.get("旧词典版本", "")),
                scenarios=split_terms(sample.get("场景证据")),
                current_labels=split_terms(sample.get("现有标签")),
            )
            self._apply_verdict(sample, verdict)
            verdicts.append({"样本编号": sample.get("样本编号"), **verdict})

        self._release(batch)
        self._refresh_batch(batch, auditor=auditor)
        round_no = len([
            row for row in store.rows(CONFIRM_MODULE)
            if str(row.get("返修批次")) == str(batch_no)
        ]) + 1
        confirmation = {
            "id": _next_id(CONFIRM_MODULE),
            "确认单号": confirmation_no,
            "返修批次": batch_no,
            "词典版本": version,
            "审核人": auditor,
            "轮次": round_no,
            "确认时间": _now(),
            "裁定明细": verdicts,
        }
        store.rows(CONFIRM_MODULE).append(confirmation)
        self._refresh_fetch_task(batch)
        approved = sum(1 for item in verdicts if item["结论"] == "核定通过")
        pending = sum(1 for item in verdicts if item["结论"] == "待核定")
        return {
            "duplicated": False,
            "确认单号": confirmation_no,
            "返修批次": batch_no,
            "词典版本": version,
            "轮次": round_no,
            "本次核定通过": approved,
            "本次待核定": pending,
            "裁定明细": verdicts,
        }, f"返修确认 {confirmation_no} 已提交：核定通过 {approved}，待核定 {pending}"

    def _apply_verdict(self, sample: dict[str, Any], verdict: dict[str, Any]) -> None:
        conclusion = verdict["结论"]
        sample["核定说明"] = verdict["说明"]
        sample["核定时间"] = _now()
        if conclusion == "核定通过":
            sample["新标签值"] = verdict["新标签值"]
            sample["status"] = "已核定"
        elif conclusion == "驳回":
            sample["status"] = "已驳回"
        else:
            sample["status"] = "待核定"
        sample["pending"] = str(sample["status"]) not in ("已领取", "已驳回")

    # ------------------------------------------------------------------
    # 放行：按返修批交付规则处理已核定样本
    # ------------------------------------------------------------------
    def _release(self, batch: dict[str, Any]) -> None:
        samples = self.batch_samples(str(batch.get("批次编号")))
        mode = str(batch.get("交付规则") or PARTIAL)
        if mode == WHOLE:
            # 整批交付：尚有未核定样本时，已核定样本等待，整批一起放行
            if any(str(s.get("status")) in SAMPLE_UNRESOLVED for s in samples):
                return
        for sample in samples:
            if str(sample.get("status")) == "已核定":
                sample["status"] = "已放行"
                sample["放行时间"] = _now()

    def _refresh_batch(self, batch: dict[str, Any], *, auditor: str) -> None:
        samples = self.batch_samples(str(batch.get("批次编号")))
        approved = [s for s in samples if str(s.get("status")) in SAMPLE_APPROVED]
        batch["样本数量"] = len(samples)
        batch["通过数量"] = len(approved)
        batch["待核定数量"] = sum(1 for s in samples if str(s.get("status")) == "待核定")
        batch["驳回数量"] = sum(1 for s in samples if str(s.get("status")) == "已驳回")
        dist: dict[str, int] = {}
        for sample in approved:
            value = str(sample.get("新标签值") or sample.get("旧标签值", ""))
            dist[value] = dist.get(value, 0) + 1
        batch["标签分布"] = ",".join(f"{key}:{dist[key]}" for key in sorted(dist))
        batch["审核人"] = auditor
        batch["审核时间"] = _now()
        if not any(str(s.get("status")) in SAMPLE_UNRESOLVED for s in samples):
            batch["status"] = "已通过"
            batch["pending"] = False
        else:
            batch["status"] = "返修中"
            batch["pending"] = True

    def _refresh_fetch_task(self, batch: dict[str, Any]) -> None:
        fetch = self.fetch_task_for_batch(str(batch.get("批次编号")))
        if fetch is None:
            return
        samples = self.batch_samples(str(batch.get("批次编号")))
        released = sum(1 for s in samples if str(s.get("status")) == "已放行")
        delivered = sum(1 for s in samples if str(s.get("status")) == "已领取")
        fetch["可领取数量"] = released
        fetch["已领取数量"] = delivered
        fetch["词典版本"] = batch.get("词典版本", fetch.get("词典版本", ""))
        fetch["交付规则"] = batch.get("交付规则", fetch.get("交付规则", ""))
        approved = int(batch.get("通过数量", 0) or 0)
        if released > 0 and delivered > 0:
            fetch["status"] = "部分领取"
        elif released > 0:
            fetch["status"] = "待领取"
        elif approved > 0 and delivered >= approved and str(batch.get("status")) == "已通过":
            fetch["status"] = "已领完"
        else:
            fetch["status"] = "待放行"
        fetch["pending"] = fetch["status"] != "已领完"

    # ------------------------------------------------------------------
    # 更正：同一份更正重复提交只生效一次
    # ------------------------------------------------------------------
    def submit_correction(
        self, *, correction_no: str, sample_no: str, scenarios: list[str]
    ) -> tuple[dict[str, Any] | None, str]:
        for row in store.rows(CORRECTION_MODULE):
            if str(row.get("更正单号")) == str(correction_no):
                return {"duplicated": True, "更正单号": correction_no,
                        "样本编号": sample_no}, f"更正 {correction_no} 已生效过，重复提交不再处理"
        sample = self.find_sample(sample_no)
        if sample is None:
            return None, f"返修样本 {sample_no} 不存在或已归档"
        if str(sample.get("status")) not in SAMPLE_UNRESOLVED:
            return None, f"样本 {sample_no} 当前状态 {sample.get('status')} 不接受更正"
        merged = list(dict.fromkeys(
            [*split_terms(sample.get("场景证据")), *split_terms(scenarios)]
        ))
        sample["场景证据"] = merged
        sample["status"] = "待复核"
        sample["pending"] = True
        correction = {
            "id": _next_id(CORRECTION_MODULE),
            "更正单号": correction_no,
            "样本编号": sample_no,
            "返修批次": sample.get("返修批次", ""),
            "场景证据": merged,
            "提交时间": _now(),
        }
        store.rows(CORRECTION_MODULE).append(correction)
        return {"duplicated": False, **correction}, f"更正 {correction_no} 已提交，样本 {sample_no} 待复核"

    # ------------------------------------------------------------------
    # 训练领取：按实际已放行的分批或整批结果领取样本
    # ------------------------------------------------------------------
    def collect(
        self, *, fetch_no: str, operator: str
    ) -> tuple[dict[str, Any] | None, str]:
        fetch = self.fetch_task(fetch_no)
        if fetch is None:
            return None, f"领取任务 {fetch_no} 不存在或已归档"
        batch_no = str(fetch.get("返修批次"))
        batch = self.review_batch(batch_no)
        if batch is None:
            return None, f"领取任务 {fetch_no} 关联的返修批次 {batch_no} 不存在"
        # 两处入口关联同一返修批次和词典版本
        fetch["词典版本"] = batch.get("词典版本", fetch.get("词典版本", ""))
        fetch["交付规则"] = batch.get("交付规则", fetch.get("交付规则", ""))
        version = str(fetch.get("词典版本", ""))
        mode = str(fetch.get("交付规则") or PARTIAL)
        samples = self.batch_samples(batch_no)

        if mode == WHOLE and any(str(s.get("status")) in SAMPLE_UNRESOLVED for s in samples):
            self._refresh_fetch_task(batch)
            return {"delivered": [], "waiting": True, "返修批次": batch_no,
                    "词典版本": version, "交付规则": mode}, (
                f"返修批 {batch_no} 存在待核定样本，整批交付需等批放行")

        releasable = [s for s in samples if str(s.get("status")) == "已放行"]
        if not releasable:
            self._refresh_fetch_task(batch)
            return {"delivered": [], "waiting": False, "返修批次": batch_no,
                    "词典版本": version, "交付规则": mode}, (
                "暂无可领取样本：待核定样本不能进入训练，已领取样本不重复交付")

        seq = len([
            row for row in store.rows(ITEM_MODULE)
            if str(row.get("返修批次")) == batch_no
        ])
        ticket = f"{fetch_no}-D{seq + 1:03d}"
        delivered: list[dict[str, Any]] = []
        for sample in releasable:
            sample["status"] = "已领取"
            sample["pending"] = False
            sample["领取人"] = operator
            sample["领取时间"] = _now()
            item = {
                "id": _next_id(ITEM_MODULE),
                "status": "已交付",
                "pending": False,
                "abnormal": False,
                "领取单号": ticket,
                "返修批次": batch_no,
                "词典版本": version,
                "样本编号": sample.get("样本编号"),
                "标签值": sample.get("新标签值") or sample.get("旧标签值", ""),
                "领取人": operator,
                "领取时间": sample["领取时间"],
            }
            store.rows(ITEM_MODULE).append(item)
            delivered.append({"样本编号": item["样本编号"], "标签值": item["标签值"],
                              "领取单号": ticket})
        fetch["最近领取时间"] = _now()
        fetch["领取人"] = operator
        self._refresh_fetch_task(batch)
        return {"delivered": delivered, "waiting": False, "返修批次": batch_no,
                "词典版本": version, "交付规则": mode,
                "领取单号": ticket}, f"已领取 {len(delivered)} 个样本（{mode}）"

    # ------------------------------------------------------------------
    # 对账与历史回查
    # ------------------------------------------------------------------
    def consistency(self, batch_no: str) -> dict[str, Any] | None:
        batch = self.review_batch(batch_no)
        if batch is None:
            return None
        samples = self.batch_samples(batch_no)
        items = [
            row for row in store.rows(ITEM_MODULE)
            if str(row.get("返修批次")) == str(batch_no)
        ]
        released = sum(1 for s in samples if str(s.get("status")) == "已放行")
        delivered = sum(1 for s in samples if str(s.get("status")) == "已领取")
        approved = int(batch.get("通过数量", 0) or 0)
        consistent = (
            delivered == len(items)
            and delivered + released <= approved
            and approved + int(batch.get("待核定数量", 0) or 0)
                + int(batch.get("驳回数量", 0) or 0)
                + sum(1 for s in samples if str(s.get("status")) in ("待审核", "待复核"))
            == len(samples)
        )
        return {
            "批次编号": batch_no,
            "词典版本": batch.get("词典版本", ""),
            "交付规则": batch.get("交付规则", ""),
            "审核通过数量": approved,
            "待核定数量": batch.get("待核定数量", 0),
            "已放行数量": released,
            "已领取数量": delivered,
            "领取明细数量": len(items),
            "一致": consistent,
        }

    def audit_source(self, batch_no: str) -> dict[str, Any] | None:
        """审核来源回查：历史批的词典版本、标签与通过数独立可查。"""
        batch = self.review_batch(batch_no)
        if batch is None:
            return None
        return {
            "批次编号": batch.get("批次编号", ""),
            "批次类型": batch.get("批次类型", ""),
            "词典版本": batch.get("词典版本", ""),
            "标签分布": batch.get("标签分布", ""),
            "样本数量": batch.get("样本数量", 0),
            "通过数量": batch.get("通过数量", 0),
            "驳回数量": batch.get("驳回数量", 0),
            "审核人": batch.get("审核人", ""),
            "审核时间": batch.get("审核时间", ""),
        }

    def rework_detail(self, batch_no: str) -> dict[str, Any] | None:
        batch = self.review_batch(batch_no)
        if batch is None:
            return None
        version = str(batch.get("词典版本", ""))
        return {
            "批次": batch,
            "样本": self.batch_samples(batch_no),
            "校验明细": dict_catalog.validation_details(version),
            "对账": self.consistency(batch_no),
        }


rework_flow = ReworkFlowService()
