"""返修批 + 词典分版校验 + 训练领取的验收测试。

场景：把 missing 拆成 unknown 与 omitted。
- 历史样本 A 保留原结论（历史批 REV-H001，词典 DICT-V1，missing:1）；
- 返修批 REV-R001（分批交付）中 A 带明确场景，B 只带旧值；
- 提交审核后立即领取：分批批取得 A，B 待核不可领；整批批 REV-R002 需等批；
- 更正 B 的场景证据并再次审核后，训练入口继续领取，A 不被重复交付；
- 旧批的版本、标签与通过数仍可独立回查。
"""
from __future__ import annotations

import unittest

from app.services.dict_catalog import dict_catalog
from app.services.label_dict import LabelDictService
from app.services.rework_flow import rework_flow
from app.store import store


def setUpModule() -> None:
    store.reset()


class ReworkFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def sample(self, sample_no: str) -> dict:
        row = rework_flow.find_sample(sample_no)
        self.assertIsNotNone(row, f"样本 {sample_no} 应存在")
        return row

    def batch(self, batch_no: str) -> dict:
        row = rework_flow.review_batch(batch_no)
        self.assertIsNotNone(row, f"批次 {batch_no} 应存在")
        return row

    # ------------------------------------------------------------------
    # 1. 词典校验明细：面向历史标签值和新标签的分版校验
    # ------------------------------------------------------------------
    def test_validation_details_are_versioned(self):
        v1 = {d["标签值"]: d for d in dict_catalog.validation_details("DICT-V1")}
        v2 = {d["标签值"]: d for d in dict_catalog.validation_details("DICT-V2")}

        # 历史版本：missing 仍是启用标签
        self.assertEqual(v1["missing"]["启用状态"], "启用")
        self.assertEqual(v1["missing"]["词典版本"], "DICT-V1")

        # 新版本：missing 停用并拆分为 unknown 与 omitted
        self.assertEqual(v2["missing"]["启用状态"], "停用")
        self.assertEqual(v2["missing"]["拆分目标"], ["unknown", "omitted"])

        # 新标签资格由适用范围与互斥条件判定：明细给出场景、层级、互斥组
        self.assertEqual(v2["unknown"]["适用场景"], ["传感器离线", "设备故障"])
        self.assertEqual(v2["omitted"]["适用场景"], ["用户跳过", "无需采集"])
        self.assertEqual(v2["unknown"]["标签层级"], "L2")
        self.assertEqual(v2["unknown"]["互斥组"], "GAP-G01")
        self.assertEqual(v2["omitted"]["互斥组"], "GAP-G01")

    # ------------------------------------------------------------------
    # 2. 主场景：分批交付批 REV-R001
    # ------------------------------------------------------------------
    def test_partial_delivery_scenario(self):
        # 审核员只提交一次返修确认：A 带明确场景 → 核定 unknown；B 只带旧值 → 待核定
        result, message = rework_flow.confirm_rework(
            batch_no="REV-R001", confirmation_no="REV-R001-C1", auditor="王审")
        self.assertFalse(result["duplicated"])
        self.assertEqual(result["本次核定通过"], 1)
        self.assertEqual(result["本次待核定"], 1)

        sample_a = self.sample("SMP-A")
        sample_b = self.sample("SMP-B")
        self.assertEqual(sample_a["status"], "已放行")  # 分批交付：核定即放行
        self.assertEqual(sample_a["新标签值"], "unknown")
        self.assertEqual(sample_b["status"], "待核定")
        self.assertIn("缺少场景证据", sample_b["核定说明"])

        batch = self.batch("REV-R001")
        self.assertEqual(batch["通过数量"], 1)
        self.assertEqual(batch["待核定数量"], 1)
        self.assertEqual(batch["status"], "返修中")

        # 重复提交同一份确认只生效一次
        again, message = rework_flow.confirm_rework(
            batch_no="REV-R001", confirmation_no="REV-R001-C1", auditor="王审")
        self.assertTrue(again["duplicated"])
        self.assertEqual(self.batch("REV-R001")["通过数量"], 1)

        # 提交审核后立即尝试领取 A：分批交付 → 取得 A；B 待核始终不可领
        collected, message = rework_flow.collect(fetch_no="FETCH-R001", operator="训练员小赵")
        self.assertFalse(collected["waiting"])
        self.assertEqual([d["样本编号"] for d in collected["delivered"]], ["SMP-A"])
        self.assertEqual(collected["delivered"][0]["标签值"], "unknown")
        # 两处入口关联同一返修批次和词典版本
        self.assertEqual(collected["返修批次"], "REV-R001")
        self.assertEqual(collected["词典版本"], "DICT-V2")
        self.assertEqual(self.sample("SMP-A")["status"], "已领取")
        self.assertEqual(self.sample("SMP-B")["status"], "待核定")

        # 再次领取：A 不重复交付，B 仍待核定不可领
        collected2, _ = rework_flow.collect(fetch_no="FETCH-R001", operator="训练员小赵")
        self.assertEqual(collected2["delivered"], [])

        # 更正 B 的场景证据；同一份更正重复提交只生效一次
        corr, _ = rework_flow.submit_correction(
            correction_no="CORR-B-1", sample_no="SMP-B", scenarios=["用户跳过"])
        self.assertFalse(corr["duplicated"])
        self.assertEqual(self.sample("SMP-B")["status"], "待复核")
        corr_dup, message = rework_flow.submit_correction(
            correction_no="CORR-B-1", sample_no="SMP-B", scenarios=["用户跳过"])
        self.assertTrue(corr_dup["duplicated"])
        self.assertEqual(self.sample("SMP-B")["场景证据"], ["用户跳过"])

        # 再次审核：B 按场景证据核定为 omitted，批次完成
        result2, _ = rework_flow.confirm_rework(
            batch_no="REV-R001", confirmation_no="REV-R001-C2", auditor="王审")
        self.assertEqual(result2["本次核定通过"], 1)
        self.assertEqual(self.sample("SMP-B")["新标签值"], "omitted")
        self.assertEqual(self.sample("SMP-B")["status"], "已放行")
        batch = self.batch("REV-R001")
        self.assertEqual(batch["status"], "已通过")
        self.assertEqual(batch["通过数量"], 2)

        # 训练入口继续领取：只取得 B，A 未被重复交付
        collected3, _ = rework_flow.collect(fetch_no="FETCH-R001", operator="训练员小赵")
        self.assertEqual([d["样本编号"] for d in collected3["delivered"]], ["SMP-B"])
        self.assertEqual(collected3["delivered"][0]["标签值"], "omitted")
        collected4, _ = rework_flow.collect(fetch_no="FETCH-R001", operator="训练员小赵")
        self.assertEqual(collected4["delivered"], [])

        # 对账：审核通过数与训练领取数一致
        consistency = rework_flow.consistency("REV-R001")
        self.assertEqual(consistency["审核通过数量"], 2)
        self.assertEqual(consistency["已领取数量"], 2)
        self.assertEqual(consistency["领取明细数量"], 2)
        self.assertTrue(consistency["一致"])

        # 历史样本 A 保留原结论：旧标签原值与使用的词典版本都还在
        self.assertEqual(self.sample("SMP-A")["旧标签值"], "missing")
        self.assertEqual(self.sample("SMP-A")["旧词典版本"], "DICT-V1")

        # 旧批的版本、标签与通过数仍可独立回查
        source = rework_flow.audit_source("REV-H001")
        self.assertEqual(source["词典版本"], "DICT-V1")
        self.assertEqual(source["标签分布"], "missing:1")
        self.assertEqual(source["通过数量"], 1)

    # ------------------------------------------------------------------
    # 3. 整批交付批 REV-R002：B 待核时需要等批
    # ------------------------------------------------------------------
    def test_whole_batch_waits_for_pending(self):
        result, _ = rework_flow.confirm_rework(
            batch_no="REV-R002", confirmation_no="REV-R002-C1", auditor="李审")
        self.assertEqual(result["本次核定通过"], 1)
        self.assertEqual(result["本次待核定"], 1)

        # 整批交付：已核定的 C 在等待期间按交付规则处理（未放行），D 待核定
        self.assertEqual(self.sample("SMP-C")["status"], "已核定")
        self.assertEqual(self.sample("SMP-D")["status"], "待核定")

        # 提交审核后立即领取：因 D 待核需要等批，什么都领不到
        collected, message = rework_flow.collect(fetch_no="FETCH-R002", operator="训练员小钱")
        self.assertTrue(collected["waiting"])
        self.assertEqual(collected["delivered"], [])
        self.assertIn("等批", message)

        # 更正 D 并再次审核 → 整批放行
        rework_flow.submit_correction(
            correction_no="CORR-D-1", sample_no="SMP-D", scenarios=["无需采集"])
        rework_flow.confirm_rework(
            batch_no="REV-R002", confirmation_no="REV-R002-C2", auditor="李审")
        self.assertEqual(self.sample("SMP-C")["status"], "已放行")
        self.assertEqual(self.sample("SMP-D")["status"], "已放行")
        self.assertEqual(self.sample("SMP-D")["新标签值"], "omitted")

        # 整批领取：C 和 D 一起交付；再领为空（不重复交付）
        collected2, _ = rework_flow.collect(fetch_no="FETCH-R002", operator="训练员小钱")
        self.assertEqual(
            sorted(d["样本编号"] for d in collected2["delivered"]), ["SMP-C", "SMP-D"])
        collected3, _ = rework_flow.collect(fetch_no="FETCH-R002", operator="训练员小钱")
        self.assertEqual(collected3["delivered"], [])

        consistency = rework_flow.consistency("REV-R002")
        self.assertEqual(consistency["审核通过数量"], 2)
        self.assertEqual(consistency["已领取数量"], 2)
        self.assertTrue(consistency["一致"])

    # ------------------------------------------------------------------
    # 4. 停用后的新录入不能继承旧值的资格
    # ------------------------------------------------------------------
    def test_deprecated_value_no_inheritance(self):
        # 样本在 DICT-V2 停用后才录入 missing：驳回，不能继承旧值资格
        verdict = dict_catalog.adjudicate(
            version="DICT-V2", old_value="missing", recorded_version="DICT-V2",
            scenarios=["传感器离线"], current_labels=["missing"])
        self.assertEqual(verdict["结论"], "驳回")
        self.assertIn("不能继承旧值资格", verdict["说明"])

        # 历史录入（DICT-V1）的同样证据则正常拆分
        verdict_ok = dict_catalog.adjudicate(
            version="DICT-V2", old_value="missing", recorded_version="DICT-V1",
            scenarios=["传感器离线"], current_labels=["missing"])
        self.assertEqual(verdict_ok["结论"], "核定通过")
        self.assertEqual(verdict_ok["新标签值"], "unknown")

        # 词典侧：新录入已停用标签值被拦截
        entry, errors = LabelDictService().create_entry({
            "词典编号": "DICT-V2", "词典版本": "DICT-V2",
            "标签名称": "缺失状态", "标签值": "missing",
        })
        self.assertIsNone(entry)
        self.assertIn("不能继承旧值资格", errors[0])

    # ------------------------------------------------------------------
    # 5. 新标签资格由适用范围和互斥条件判定
    # ------------------------------------------------------------------
    def test_scope_and_mutex_gate_new_label(self):
        # 场景同时命中两个拆分值的适用范围 → 无法唯一判定，待核定
        verdict = dict_catalog.adjudicate(
            version="DICT-V2", old_value="missing", recorded_version="DICT-V1",
            scenarios=["传感器离线", "用户跳过"], current_labels=["missing"])
        self.assertEqual(verdict["结论"], "待核定")
        self.assertIn("多个拆分值", verdict["说明"])

        # 场景不在任何拆分值适用范围 → 待核定
        verdict = dict_catalog.adjudicate(
            version="DICT-V2", old_value="missing", recorded_version="DICT-V1",
            scenarios=["网络抖动"], current_labels=["missing"])
        self.assertEqual(verdict["结论"], "待核定")
        self.assertIn("未覆盖", verdict["说明"])

        # 现有标签与候选新值同属互斥组 → 互斥冲突，待核定
        verdict = dict_catalog.adjudicate(
            version="DICT-V2", old_value="missing", recorded_version="DICT-V1",
            scenarios=["传感器离线"], current_labels=["missing", "omitted"])
        self.assertEqual(verdict["结论"], "待核定")
        self.assertIn("互斥组", verdict["说明"])

    # ------------------------------------------------------------------
    # 6. 待核定样本绝不能进入训练（贯穿主场景再独立断言一次）
    # ------------------------------------------------------------------
    def test_pending_sample_never_enters_training(self):
        rework_flow.confirm_rework(
            batch_no="REV-R001", confirmation_no="REV-R001-C1", auditor="王审")
        for _ in range(3):
            collected, _ = rework_flow.collect(fetch_no="FETCH-R001", operator="训练员")
            delivered = [d["样本编号"] for d in collected["delivered"]]
            self.assertNotIn("SMP-B", delivered)
        items = [
            row for row in store.rows("train_fetch_item")
            if row["返修批次"] == "REV-R001"
        ]
        self.assertEqual([item["样本编号"] for item in items], ["SMP-A"])

    # ------------------------------------------------------------------
    # 7. 路由注册冒烟：两处入口与校验明细接口都在
    # ------------------------------------------------------------------
    def test_app_routes_registered(self):
        from app.main import app

        def walk(routes):
            for route in routes:
                path = getattr(route, "path", None)
                if path:
                    yield path
                inner = getattr(route, "original_router", route)
                sub = getattr(inner, "routes", None)
                if sub:
                    yield from walk(sub)

        paths = set(walk(app.routes))
        self.assertIn("/api/label_dict/validation-details", paths)
        self.assertIn("/api/label_review/{batch_no}/rework-confirm", paths)
        self.assertIn("/api/rework_sample/{sample_no}/corrections", paths)
        self.assertIn("/api/train_fetch/{fetch_no}/collect", paths)
        self.assertIn("/api/label_review/{batch_no}/audit-source", paths)


if __name__ == "__main__":
    unittest.main()
