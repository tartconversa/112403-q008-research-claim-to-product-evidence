"""端到端领域规则测试。"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidence_governance.cli import main  # noqa: E402
from evidence_governance.csv_import import registry_from_samples  # noqa: E402
from evidence_governance.demo import build_demo_registry  # noqa: E402
from evidence_governance.evaluation import evaluate_claim_use  # noqa: E402
from evidence_governance.models import EvidenceRef  # noqa: E402
from evidence_governance.service import GovernanceService  # noqa: E402
from evidence_governance.store import Registry  # noqa: E402
from evidence_governance.utils import strength_matches  # noqa: E402

SAMPLES = ROOT / "samples"
DAY = "2026-11-25"


def make_verified_world(service: GovernanceService, prefix: str = "T") -> dict[str, str]:
    """建立一个证据、适用范围、审批和发布均完整的最小世界。"""
    ids = {
        "material": f"{prefix}-M",
        "batch": f"{prefix}-B",
        "experiment": f"{prefix}-E",
        "applicability": f"{prefix}-APP",
        "formula": f"{prefix}-F",
        "product": f"{prefix}-P",
        "pv": f"{prefix}-PV",
        "claim": f"{prefix}-C",
        "approval": f"{prefix}-A",
        "asset": f"{prefix}-ASSET",
        "publication": f"{prefix}-PUB",
    }
    service.register_material(ids["material"], "显式原料")
    service.register_material_batch(ids["batch"], ids["material"])
    service.register_experiment(
        ids["experiment"],
        version=1,
        material_id=ids["material"],
        concentration="2%",
        condition="lab-standard",
        result="positive",
    )
    service.register_applicability(
        ids["experiment"],
        ids["material"],
        applicability_id=ids["applicability"],
        scope="material_wide",
        concentration="2%",
        condition="lab-standard",
        confirmed_by="rd-test",
        confirmed_on="2026-01-02",
    )
    service.register_formula(ids["formula"], components=[{"material_id": ids["material"], "concentration": "2%"}])
    service.register_product(ids["product"], "测试商品")
    service.register_product_version(ids["pv"], ids["product"], ids["formula"], effective_from="2026-01-01")
    service.register_material_lot_use(ids["pv"], ids["material"], ids["batch"])
    service.create_claim(ids["claim"], "限定实验条件下的已验证表述。", on="2026-01-03")
    service.link_claim_evidence(
        ids["claim"], ids["experiment"], 1, applicability_id=ids["applicability"]
    )
    service.approve_claim(
        ids["approval"],
        ids["claim"],
        "compliance-test",
        decided_on="2026-01-04",
        valid_from="2026-01-05",
        valid_to="2027-01-04",
        channels=["web"],
        product_ids=[ids["product"]],
        product_version_ids=[ids["pv"]],
        formula_version_ids=[ids["formula"]],
    )
    service.register_asset(ids["asset"], "测试页面")
    service.publish_claim(
        ids["publication"],
        ids["asset"],
        ids["claim"],
        channel="web",
        published_on="2026-01-10",
        product_version_id=ids["pv"],
    )
    return ids


class GovernanceRulesTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.service = GovernanceService(self.registry)

    def test_complete_chain_is_currently_valid(self):
        ids = make_verified_world(self.service)
        report = evaluate_claim_use(
            self.registry,
            ids["claim"],
            DAY,
            channel="web",
            product_version_id=ids["pv"],
        )
        self.assertTrue(report["usable"], json.dumps(report["issues"], ensure_ascii=False))
        self.assertTrue(report["evidence_verified"])
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "valid")
        self.assertTrue(state["currently_valid"])

        guide = self.service.claim_usage_guide(ids["claim"], DAY)
        approval = guide["approvals"][0]
        self.assertEqual(approval["channels"], ["web"])
        self.assertEqual(approval["product_versions"][0]["id"], ids["pv"])
        self.assertEqual(approval["valid_to"], "2027-01-04")

    def test_same_keywords_do_not_establish_support(self):
        self.service.register_material("M1", "名称完全不同甲", keywords=["same-keyword", "抗氧化"])
        self.service.register_material("M2", "名称完全不同乙", keywords=["same-keyword", "抗氧化"])
        self.service.register_experiment("E1", material_id="M1", concentration="2%", condition="c")
        self.service.register_applicability(
            "E1", "M1", applicability_id="A1", scope="material_wide",
            confirmed_by="rd", concentration="2%", condition="c",
        )
        self.service.register_formula("F1", components=[{"material_id": "M2", "concentration": "2%"}])
        self.service.register_product("P1")
        self.service.register_product_version("PV1", "P1", "F1")
        self.service.register_material_lot_use("PV1", "M2")
        self.service.create_claim("C1", "关键词相似不等于证据支持")
        self.service.link_claim_evidence("C1", "E1", 1, applicability_id="A1")
        self.service.approve_claim(
            "AP1", "C1", "legal", valid_from="2026-01-01", channels=["web"],
            product_version_ids=["PV1"], formula_version_ids=["F1"],
        )
        report = evaluate_claim_use(self.registry, "C1", DAY, channel="web", product_version_id="PV1")
        self.assertFalse(report["usable"])
        self.assertIn("material_scope_mismatch", [i["code"] for i in report["blockers"]])

    def test_batch_and_concentration_conditions_are_enforced(self):
        ids = make_verified_world(self.service, "COND")
        # 把已确认适用范围改成仅 B-OLD 批次且浓度 1%，而目标是 B 批次、2%。
        app = self.registry.get("applicabilities", ids["applicability"])
        app.scope = "batch_specific"
        app.material_batch_id = "COND-B-OTHER"
        self.service.register_material_batch("COND-B-OTHER", ids["material"])
        app.concentration = "1%"
        self.registry.add("applicabilities", app, replace=True)

        report = evaluate_claim_use(
            self.registry, ids["claim"], DAY, channel="web", product_version_id=ids["pv"]
        )
        codes = {i["code"] for i in report["blockers"]}
        self.assertIn("material_batch_mismatch", codes)
        self.assertIn("concentration_mismatch", codes)
        self.assertFalse(report["usable"])

    def test_missing_versions_and_relations_are_marked(self):
        self.service.register_material("M")
        self.service.register_experiment("E", version=1, material_id="M", concentration="2%", condition="c")
        self.service.register_formula("F")
        self.service.register_product("P")
        self.service.register_product_version("PV", "P", "F")
        self.service.create_claim("C", "版本不明")
        self.service.approve_claim(
            "AP",
            "C",
            "legal",
            valid_from="2026-01-01",
            channels=["web"],
            product_version_ids=["PV"],
            formula_version_ids=["F"],
            evidence_refs=[EvidenceRef("E", 2, version_explicit=False)],
        )
        report = evaluate_claim_use(self.registry, "C", DAY, approval_id="AP", product_version_id="PV")
        codes = {i["code"] for i in report["issues"]}
        self.assertIn("evidence_version_missing", codes)
        self.assertIn("evidence_version_not_explicit", codes)
        self.assertFalse(report["evidence_verified"])

    def test_new_experiment_overturn_lists_active_page_and_snapshot_survives(self):
        ids = make_verified_world(self.service, "OV")
        before = self.service.reconstruct_publication(ids["publication"])
        self.assertEqual(
            before["snapshot"]["evidence_at_publication"][0]["version"]["status"], "active"
        )

        self.service.register_experiment("OV-NEW", version=1, material_id=ids["material"],
                                         concentration="2%", condition="lab-standard", result="negative")
        self.service.overturn_experiment(
            ids["experiment"], new_experiment_id="OV-NEW", reason="相反结果", on="2026-11-01"
        )
        open_cases = self.service.scan_for_review(DAY)
        codes = {(case.code, case.publication_id) for case in open_cases}
        self.assertIn(("evidence_overturned", ids["publication"]), codes)

        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "blocked")
        self.assertFalse(state["currently_valid"])

        after = self.service.reconstruct_publication(ids["publication"])
        self.assertEqual(
            after["snapshot"]["evidence_at_publication"][0]["version"]["status"], "active",
            "不可变快照不能被后续推翻操作改写",
        )

    def test_formula_upgrade_requires_owner_review_but_legacy_scope_can_be_confirmed(self):
        ids = make_verified_world(self.service, "FUP")
        # 商品当前版本切换到 F2；旧发布物仍指向 F1/PV。
        self.service.register_formula("FUP-F2", parent_id=ids["formula"], effective_from="2026-07-01")
        self.service.confirm_formula_lineage("FUP-F2", ids["formula"], "owner", "2026-07-01")
        self.service.register_product_version("FUP-PV2", ids["product"], "FUP-F2", effective_from="2026-07-01")

        cases = self.service.scan_for_review(DAY)
        formula_case = next(c for c in cases if c.code == "formula_changed")
        self.assertEqual(formula_case.publication_id, ids["publication"])
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "needs_review")
        self.assertFalse(state["currently_valid"])

        self.service.resolve_review_case(
            formula_case.id, "product-owner", "legacy_confirmed",
            "确认该物料仅随仍在售的旧批次展示", DAY,
        )
        self.service.scan_for_review(DAY)
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "valid")

    def test_expired_approval_and_takedown_clears_dashboard(self):
        ids = make_verified_world(self.service, "EXP")
        approval = self.registry.get("approvals", ids["approval"])
        approval.valid_to = "2026-07-01"
        self.registry.add("approvals", approval, replace=True)

        cases = self.service.scan_for_review(DAY)
        expired = next(c for c in cases if c.code == "approval_expired")
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "blocked")

        self.service.resolve_review_case(
            expired.id, "owner", "removed", "下线旧页面", DAY
        )
        remaining = self.service.scan_for_review(DAY)
        self.assertEqual(remaining, [])
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "removed")
        self.assertFalse(state["currently_valid"])

    def test_applicability_can_be_confirmed_later(self):
        self.service.register_material("M", "原料")
        self.service.register_experiment("E", version=1, material_id="M", concentration="2%", condition="c")
        app = self.service.register_applicability(
            "E", "M", applicability_id="A", scope="material_wide",
            concentration="2%", condition="c",
        )
        self.assertIn("rd_confirmation", app.missing)
        confirmed = self.service.confirm_applicability("A", "rd-chen", DAY)
        self.assertEqual(confirmed.state, "confirmed")
        self.assertEqual(confirmed.missing, [])

    def test_protocol_registration_is_linked_to_experiment(self):
        self.service.register_material("M")
        self.service.register_protocol(
            "PROTO-1", "标准仪器测试方案", method="instrument",
            concentration="2%", condition="lab", owner="rd",
        )
        self.service.register_experiment(
            "E-P", protocol_id="PROTO-1", material_id="M",
            concentration="2%", condition="lab",
        )
        protocol = self.registry.get("protocols", "PROTO-1")
        experiment = self.registry.get("experiments", "E-P")
        self.assertEqual(protocol.method, "instrument")
        self.assertEqual(experiment.versions[0].protocol_id, "PROTO-1")

    def test_expired_product_version_without_successor_is_not_valid(self):
        ids = make_verified_world(self.service, "WIN")
        pv = self.registry.get("product_versions", ids["pv"])
        pv.effective_to = "2026-06-30"
        self.registry.add("product_versions", pv, replace=True)

        report = evaluate_claim_use(
            self.registry, ids["claim"], DAY,
            channel="web", product_version_id=ids["pv"],
        )
        self.assertIn("product_version_window_ended", {i["code"] for i in report["warnings"]})
        self.assertFalse(report["usable"])
        self.assertTrue(report["legacy_scope_usable"])

        cases = self.service.scan_for_review(DAY)
        self.assertTrue(any(c.code == "product_version_window_ended" for c in cases))
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "needs_review")

        case = next(c for c in cases if c.code == "product_version_window_ended")
        self.service.resolve_review_case(
            case.id, "owner", "legacy_confirmed", "仍有旧批次在售", DAY
        )
        # 再次扫描不能把负责人已明确按旧批次保留结案的窗口问题重新打开。
        self.service.scan_for_review(DAY)
        resolved_case = self.registry.get("review_cases", case.id)
        self.assertEqual(resolved_case.status, "resolved")
        state = self.service.publication_display_state(ids["publication"], DAY)
        self.assertEqual(state["display_state"], "valid")

    def test_sales_batch_only_publication_keeps_product_version_for_drift_scan(self):
        ids = make_verified_world(self.service, "SBONLY")
        self.service.register_sales_batch("SB-ONLY", ids["product"], ids["pv"])
        self.service.confirm_sales_batch_inheritance("SB-ONLY", "owner", DAY)
        self.service.register_asset("ASSET-SB", "批次物料")
        self.service.publish_claim(
            "PUB-SB", "ASSET-SB", ids["claim"],
            channel="web", published_on="2026-02-01", sales_batch_id="SB-ONLY",
        )
        pub = self.registry.get("publications", "PUB-SB")
        self.assertEqual(pub.product_version_id, ids["pv"])

        self.service.register_formula("F-NEW")
        self.service.register_product_version("PV-NEW", ids["product"], "F-NEW", effective_from="2026-07-01")
        cases = self.service.scan_for_review(DAY)
        drift = [c for c in cases if c.publication_id == "PUB-SB" and c.code == "formula_changed"]
        self.assertEqual(len(drift), 1)

    def test_material_lot_use_window_is_checked_at_review_date(self):
        ids = make_verified_world(self.service, "LOTWIN")
        lot_use = self.registry.data.material_lot_uses[0]
        lot_use.valid_from = "2026-01-01"
        lot_use.valid_to = "2026-06-30"
        self.registry.add("material_lot_uses", lot_use, replace=True)

        self.service.register_material_batch("B-NEW", ids["material"])
        self.service.register_material_lot_use(
            ids["pv"], ids["material"], "B-NEW", valid_from="2026-07-01"
        )
        app = self.registry.get("applicabilities", ids["applicability"])
        app.scope = "batch_specific"
        app.material_batch_id = ids["batch"]
        self.registry.add("applicabilities", app, replace=True)

        report = evaluate_claim_use(
            self.registry, ids["claim"], DAY,
            channel="web", product_version_id=ids["pv"],
        )
        codes = {i["code"] for i in report["blockers"]}
        self.assertIn("material_batch_mismatch", codes)

    def test_incomparable_concentration_units_are_gaps_not_mismatches(self):
        self.assertIsNone(strength_matches("2%", "20 mg/g"))
        self.assertIsNone(strength_matches("2%", "2"))
        self.assertTrue(strength_matches("2.0%", "2%"))

    def test_sales_batch_inheritance_must_be_confirmed(self):
        ids = make_verified_world(self.service, "SB")
        self.service.register_sales_batch(
            "SB1", ids["product"], ids["pv"], parent_sales_batch_id=None
        )
        report = evaluate_claim_use(
            self.registry, ids["claim"], DAY,
            channel="web", product_version_id=ids["pv"], sales_batch_id="SB1",
        )
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("sales_batch_inheritance_unconfirmed", codes)
        self.assertFalse(report["usable"])

        self.service.confirm_sales_batch_inheritance("SB1", "owner", DAY)
        report = evaluate_claim_use(
            self.registry, ids["claim"], DAY,
            channel="web", product_version_id=ids["pv"], sales_batch_id="SB1",
        )
        self.assertTrue(report["usable"], json.dumps(report["issues"], ensure_ascii=False))

    def test_ended_campaign_retains_snapshot(self):
        ids = make_verified_world(self.service, "CAMP")
        self.service.create_campaign("CAMP1", "已结束活动", started_on="2026-02-01")
        self.service.register_asset("CAMP-ASSET-EXTRA", "活动物料")
        # 同一表述可生成另一个活动发布记录；结束活动后快照仍保留。
        self.service.publish_claim(
            "CAMP-EXTRA-PUB", "CAMP-ASSET-EXTRA", ids["claim"], approval_id=ids["approval"],
            channel="web", published_on="2026-03-01", product_version_id=ids["pv"],
            campaign_id="CAMP1",
        )
        self.service.end_campaign("CAMP1", "2026-05-31")
        reconstruction = self.service.reconstruct_campaign("CAMP1")
        self.assertEqual(reconstruction["campaign"]["ended_on"], "2026-05-31")
        self.assertEqual(len(reconstruction["publications"]), 1)
        snapshot = reconstruction["publications"][0]["snapshot"]
        self.assertEqual(snapshot["claim"]["id"], ids["claim"])
        self.assertIn("approval", snapshot)
        self.assertTrue(snapshot["evidence_at_publication"])

    def test_json_roundtrip(self):
        registry = build_demo_registry()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            registry.save(path)
            loaded = Registry.load(path)
            service = GovernanceService(loaded)
            dashboard = service.review_dashboard(DAY)
            self.assertGreaterEqual(dashboard["open_case_count"], 3)
            json.dumps(dashboard, ensure_ascii=False)  # 报告必须可 JSON 序列化


class SampleCsvIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.registry = registry_from_samples(SAMPLES)
        self.service = GovernanceService(self.registry)
        self.dashboard = self.service.review_dashboard(DAY)
        self.states = {item["publication_id"]: item for item in self.dashboard["publications"]}

    def test_sample_claim_coverage(self):
        guide = self.service.claim_usage_guide("C2", DAY)
        approval = next(a for a in guide["approvals"] if a["approval_id"] == "AP-C2")
        self.assertTrue(approval["currently_effective"])
        self.assertEqual(approval["channels"], ["web"])
        self.assertIsNone(approval["valid_to"])
        self.assertIn("PV:P1:F2", approval["scope"]["product_version_ids"])

    def test_sample_c1_expired_and_formula_drift(self):
        u1 = self.states["U1"]
        self.assertEqual(u1["display_state"], "blocked")
        case_codes = {
            self.registry.review_cases[cid].code
            for cid in u1["open_case_ids"]
        }
        self.assertIn("approval_expired", case_codes)
        self.assertIn("formula_changed", case_codes)

    def test_sample_c2_is_approved_but_evidence_scope_incomplete(self):
        report = evaluate_claim_use(
            self.registry, "C2", DAY, channel="web", product_version_id="PV:P1:F2"
        )
        self.assertTrue(report["approved_for_scope"])
        self.assertFalse(report["evidence_verified"])
        codes = {issue["code"] for issue in report["missing_evidence"]}
        self.assertIn("missing_batch_scope", codes)
        self.assertIn("formula_concentration_missing", codes)
        self.assertEqual(self.states["U2"]["display_state"], "needs_review")

    def test_sample_c3_material_mismatch_and_not_approved(self):
        report = evaluate_claim_use(
            self.registry, "C3", DAY, channel="live", product_version_id="PV:P2:F1"
        )
        codes = {issue["code"] for issue in report["blockers"]}
        self.assertIn("approval_not_approved", codes)
        self.assertIn("material_scope_mismatch", codes)
        self.assertEqual(self.states["U3"]["display_state"], "blocked")

    def test_ended_sample_campaign_publication_is_archived_not_current(self):
        u4 = self.states["U4"]
        self.assertEqual(u4["display_state"], "ended")
        self.assertFalse(u4["currently_valid"])
        reconstructed = self.service.reconstruct_publication("U4")
        self.assertTrue(reconstructed["snapshot"])

    def test_cli_sample_report_json(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["sample-report", "--date", DAY, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertIn("open_cases", payload)
        self.assertGreater(payload["open_case_count"], 0)


if __name__ == "__main__":
    unittest.main()
