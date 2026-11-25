"""构造一个覆盖正常发布、证据推翻、配方升级、审批到期和活动归档的合成应用状态。"""

from __future__ import annotations

from .service import GovernanceService
from .store import Registry


def build_demo_registry() -> Registry:
    registry = Registry()
    svc = GovernanceService(registry)

    # 一个当前仍完全有效的表述/页面。
    svc.register_paper("PAPER-HYD", "Synthetic hydration study", published_on="2026-01-05")
    svc.register_material("M-HYD", "合成保湿原料", keywords=["hydration", "保湿"])
    svc.register_material_batch("B-HYD-25", "M-HYD", batch_code="HYD25", received_on="2026-01-08")
    svc.register_experiment(
        "E-HYD",
        title="2% M-HYD 24小时角质层含水量测试",
        paper_id="PAPER-HYD",
        version=1,
        recorded_on="2026-01-10",
        material_id="M-HYD",
        concentration="2%",
        condition="lab-hydration-24h",
        protocol="随机对照仪器测试，n=32（合成）",
        result="positive",
        conclusion_id="CONC-HYD-24H",
    )
    svc.register_applicability(
        "E-HYD",
        "M-HYD",
        applicability_id="APP-HYD",
        scope="material_wide",
        confirmed_by="rd-chen",
        confirmed_on="2026-01-12",
        note="相同工艺和 2% 配方浓度下，R&D 确认可覆盖后续 M-HYD 批次",
    )
    svc.register_formula("F-HYD-1", components=[{"material_id": "M-HYD", "concentration": "1%"}])
    svc.register_formula(
        "F-HYD-2",
        parent_id="F-HYD-1",
        effective_from="2026-06-01",
        components=[{"material_id": "M-HYD", "concentration": "2%"}],
        change_summary="提升 M-HYD 至 2%",
    )
    svc.confirm_formula_lineage("F-HYD-2", "F-HYD-1", "owner-wang", "2026-06-01")
    svc.register_product("P-HYD", "保湿精华")
    svc.register_product_version("PV-HYD-V2", "P-HYD", "F-HYD-2", effective_from="2026-06-01")
    svc.register_material_lot_use("PV-HYD-V2", "M-HYD", "B-HYD-25")
    svc.register_sales_batch("SB-HYD-2511", "P-HYD", "PV-HYD-V2", batch_code="2511")
    svc.confirm_sales_batch_inheritance("SB-HYD-2511", "owner-wang", "2026-11-01")
    svc.create_claim(
        "CL-HYD",
        "在实验室条件下，含 2% M-HYD 的配方在使用 24 小时后测得角质层含水量提升；结果限于 lab-hydration-24h 条件。",
        on="2026-01-15",
    )
    svc.link_claim_evidence("CL-HYD", "E-HYD", 1, applicability_id="APP-HYD")
    svc.approve_claim(
        "AP-HYD",
        "CL-HYD",
        "compliance-li",
        decided_on="2026-01-18",
        valid_from="2026-01-20",
        valid_to="2027-01-19",
        channels=["web", "social", "retail-packaging"],
        product_ids=["P-HYD"],
        product_version_ids=["PV-HYD-V2"],
        formula_version_ids=["F-HYD-2"],
        sales_batch_ids=["SB-HYD-2511"],
    )
    svc.register_asset("PAGE-HYD", "保湿精华商品页", kind="page", location="/products/hyd")
    svc.publish_claim(
        "PUB-HYD",
        "PAGE-HYD",
        "CL-HYD",
        approval_id="AP-HYD",
        channel="web",
        published_on="2026-07-01",
        product_version_id="PV-HYD-V2",
        sales_batch_id="SB-HYD-2511",
    )

    # 已结束活动：发布快照和审批依据继续保留。
    svc.register_asset("MAT-SUMMER", "夏季活动长图", kind="material", location="materials/summer.png")
    svc.create_campaign("CAMP-SUMMER", "2026 夏季活动", started_on="2026-06-01")
    svc.publish_claim(
        "PUB-SUMMER",
        "MAT-SUMMER",
        "CL-HYD",
        approval_id="AP-HYD",
        channel="social",
        published_on="2026-06-01",
        product_version_id="PV-HYD-V2",
        campaign_id="CAMP-SUMMER",
    )
    svc.end_campaign("CAMP-SUMMER", "2026-08-31")

    # 新实验显式推翻旧结论；旧页面仍在线，应进入 blocked。
    svc.register_material("M-OLD", "旧原料")
    svc.register_material_batch("B-OLD-1", "M-OLD")
    svc.register_experiment(
        "E-OLD",
        version=1,
        material_id="M-OLD",
        concentration="1%",
        condition="lab-old",
        result="positive",
        conclusion_id="CONC-OLD",
    )
    svc.register_applicability(
        "E-OLD",
        "M-OLD",
        applicability_id="APP-OLD",
        scope="material_wide",
        confirmed_by="rd-zhao",
        confirmed_on="2026-01-05",
    )
    svc.register_formula("F-OLD", components=[{"material_id": "M-OLD", "concentration": "1%"}])
    svc.register_product("P-OLD", "旧品")
    svc.register_product_version("PV-OLD", "P-OLD", "F-OLD", effective_from="2026-01-01")
    svc.register_material_lot_use("PV-OLD", "M-OLD", "B-OLD-1")
    svc.create_claim("CL-OLD", "旧实验支持的旧结论。", on="2026-01-10")
    svc.link_claim_evidence("CL-OLD", "E-OLD", 1, applicability_id="APP-OLD")
    svc.approve_claim(
        "AP-OLD",
        "CL-OLD",
        "compliance-li",
        decided_on="2026-01-12",
        valid_from="2026-01-15",
        valid_to="2027-01-14",
        channels=["web"],
        product_version_ids=["PV-OLD"],
        formula_version_ids=["F-OLD"],
    )
    svc.register_asset("PAGE-OLD", "仍在线的旧结论页", location="/old")
    svc.publish_claim(
        "PUB-OLD",
        "PAGE-OLD",
        "CL-OLD",
        approval_id="AP-OLD",
        channel="web",
        published_on="2026-02-01",
        product_version_id="PV-OLD",
    )
    svc.register_experiment(
        "E-NEW",
        title="推翻旧结论的重复试验",
        version=1,
        recorded_on="2026-10-20",
        material_id="M-OLD",
        concentration="1%",
        condition="lab-old",
        result="negative",
        conclusion_id="CONC-NEW",
    )
    svc.overturn_experiment(
        "E-OLD",
        new_experiment_id="E-NEW",
        reason="更大样本、相同条件下结果相反，R&D 登记旧结论被推翻",
        actor="rd-zhao",
        on="2026-11-01",
    )

    # 配方升级后旧页面未复核：审批和证据对旧版本仍成立，但当前商品已切到新版本。
    svc.register_material("M-F", "配方追踪原料")
    svc.register_material_batch("B-F-1", "M-F")
    svc.register_experiment(
        "E-F",
        version=1,
        material_id="M-F",
        concentration="3%",
        condition="lab-f",
        result="positive",
    )
    svc.register_applicability(
        "E-F",
        "M-F",
        applicability_id="APP-F",
        scope="material_wide",
        confirmed_by="rd-chen",
        confirmed_on="2026-01-08",
    )
    svc.register_formula("F-F1", components=[{"material_id": "M-F", "concentration": "3%"}])
    svc.register_formula(
        "F-F2",
        parent_id="F-F1",
        effective_from="2026-07-01",
        components=[{"material_id": "M-F", "concentration": "3%"}],
    )
    svc.confirm_formula_lineage("F-F2", "F-F1", "owner-wang", "2026-07-01")
    svc.register_product("P-F", "配方升级商品")
    svc.register_product_version("PV-F1", "P-F", "F-F1", effective_from="2026-01-01", effective_to="2026-06-30")
    svc.register_product_version("PV-F2", "P-F", "F-F2", effective_from="2026-07-01")
    svc.register_material_lot_use("PV-F1", "M-F", "B-F-1")
    svc.create_claim("CL-F", "仅适用于 F1 旧版本的限定表述。", on="2026-01-09")
    svc.link_claim_evidence("CL-F", "E-F", 1, applicability_id="APP-F")
    svc.approve_claim(
        "AP-F",
        "CL-F",
        "compliance-li",
        valid_from="2026-01-10",
        valid_to="2027-01-09",
        channels=["web"],
        product_version_ids=["PV-F1"],
        formula_version_ids=["F-F1"],
    )
    svc.register_asset("PAGE-F-LEGACY", "未标注旧版本的配方页", location="/f")
    svc.publish_claim(
        "PUB-F-LEGACY",
        "PAGE-F-LEGACY",
        "CL-F",
        channel="web",
        published_on="2026-02-01",
        product_version_id="PV-F1",
    )

    # 审批已到期但页面仍在线。
    svc.register_material("M-EXP", "到期示例原料")
    svc.register_experiment(
        "E-EXP",
        version=1,
        material_id="M-EXP",
        concentration="1%",
        condition="lab-exp",
        result="positive",
    )
    svc.register_applicability(
        "E-EXP",
        "M-EXP",
        applicability_id="APP-EXP",
        scope="material_wide",
        confirmed_by="rd-chen",
        confirmed_on="2026-01-01",
    )
    svc.register_formula("F-EXP", components=[{"material_id": "M-EXP", "concentration": "1%"}])
    svc.register_product("P-EXP", "到期商品")
    svc.register_product_version("PV-EXP", "P-EXP", "F-EXP", effective_from="2026-01-01")
    svc.create_claim("CL-EXP", "审批期间限定表述。")
    svc.link_claim_evidence("CL-EXP", "E-EXP", 1, applicability_id="APP-EXP")
    svc.approve_claim(
        "AP-EXP",
        "CL-EXP",
        "compliance-li",
        valid_from="2026-01-01",
        valid_to="2026-07-01",
        channels=["web"],
        product_version_ids=["PV-EXP"],
        formula_version_ids=["F-EXP"],
    )
    svc.register_asset("PAGE-EXP", "到期仍在线页", location="/exp")
    svc.publish_claim(
        "PUB-EXP",
        "PAGE-EXP",
        "CL-EXP",
        channel="web",
        published_on="2026-02-01",
        product_version_id="PV-EXP",
    )

    return registry
