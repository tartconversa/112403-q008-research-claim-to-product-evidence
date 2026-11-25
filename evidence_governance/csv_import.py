"""把交付的合成 CSV 样例导入领域登记处。

导入器只做显式列映射，不根据 result 文本、名称或关键词推断“推翻/支持”关系。
样例缺少研发确认人、配方浓度和审批中的证据版本号，因此这些关系会保持
incomplete / version_explicit=False，并由评估器列为证据缺口。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import FormulaComponent
from .service import GovernanceService
from .store import Registry


def _read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig 同时兼容有无 BOM 的 CSV。
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        return [{(key or "").strip(): (value or "").strip() for key, value in row.items()} for row in reader]


def _split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    for separator in (";", "|", "，", ","):
        if separator in value:
            return [item.strip() for item in value.split(separator) if item.strip()]
    return [value.strip()]


def registry_from_samples(samples_dir: str | Path, *, include_pages: bool = True) -> Registry:
    root = Path(samples_dir)
    registry = Registry()
    service = GovernanceService(registry)

    studies = _read_csv(root / "studies.csv")
    products = _read_csv(root / "products.csv")
    claims = _read_csv(root / "claims.csv")

    study_versions: dict[str, int] = {}

    # ---- 科研论文/实验 ------------------------------------------------
    for row in studies:
        material_id = row["material"]
        if material_id and not registry.find("materials", material_id):
            service.register_material(material_id, material_id)
        version = int(row.get("version") or "1")
        study_versions[row["study"]] = version
        service.register_experiment(
            row["study"],
            title=f"实验 {row['study']}",
            version=version,
            material_id=material_id or None,
            concentration=row.get("concentration") or None,
            condition=row.get("condition") or None,
            result=row.get("result") or None,
            result_summary=row.get("result") or None,
        )
        # CSV 能证明来源行写了什么，但不能证明 R&D 已完成批次/范围确认。
        service.register_applicability(
            row["study"],
            material_id,
            applicability_id=f"APP-IMPORT-{row['study']}",
            experiment_version=version,
            scope="unspecified",
            concentration=row.get("concentration") or None,
            condition=row.get("condition") or None,
            note="由 studies.csv 导入；缺少研发确认人和批次适用范围",
        )

    # ---- 原料批次、配方/商品版本 --------------------------------------
    for row in products:
        product_id = row["product"]
        formula_id = row["formula"]
        material_id = row["material"]
        batch_id = row.get("batch") or None
        pvid = f"PV:{product_id}:{formula_id}"

        if material_id and not registry.find("materials", material_id):
            service.register_material(material_id, material_id)
        if batch_id and not registry.find("material_batches", batch_id):
            service.register_material_batch(batch_id, material_id)
        if not registry.find("products", product_id):
            service.register_product(product_id, product_id)

        formula = registry.find("formulas", formula_id)
        if formula is None:
            # products.csv 的 material/batch 是“商品版本—原料批次”的显式关联，
            # 不把它猜测成完整配方组成；完整成分/浓度可通过 register_formula 录入。
            service.register_formula(formula_id, formula_code=formula_id)

        if not registry.find("product_versions", pvid):
            service.register_product_version(
                pvid,
                product_id,
                formula_id,
                effective_from=row.get("from") or None,
                effective_to=row.get("to") or None,
            )

        use_id = f"LOT:{product_id}:{formula_id}:{batch_id or material_id}"
        if not registry.find("material_lot_uses", use_id):
            service.register_material_lot_use(
                pvid,
                material_id,
                batch_id,
                use_id=use_id,
                valid_from=row.get("from") or None,
                valid_to=row.get("to") or None,
            )

    # ---- 表述和审批 ----------------------------------------------------
    for row in claims:
        claim_id = row["claim"]
        study_id = row.get("study") or ""
        study_version = study_versions.get(study_id, 1)
        text = row.get("text") or row.get("statement") or row.get("wording")
        if not text:
            text = f"［样例未提供传播表述正文；记录号 {claim_id}，不得直接用于发布］"
        service.create_claim(claim_id, text)
        if study_id:
            service.link_claim_evidence(
                claim_id,
                study_id,
                study_version,
                # claims.csv 没有 evidence/study version 列：不能声称审批显式锁定了版本。
                version_explicit=bool(row.get("study_version") or row.get("evidence_version")),
            )

        raw_state = (row.get("state") or "").strip().lower()
        approval_state = {
            "approved": "approved",
            "expired": "approved",  # 保留批准决定，有效状态由 valid_to 时间推导
            "review": "in_review",
            "in_review": "in_review",
            "rejected": "rejected",
            "revoked": "revoked",
        }.get(raw_state, "in_review")
        product_id = row.get("product") or None
        formula_id = row.get("formula") or None
        pvid = f"PV:{product_id}:{formula_id}" if product_id and formula_id else None
        channels = _split_multi(row.get("channel") or "")
        service.approve_claim(
            f"AP-{claim_id}",
            claim_id,
            row.get("reviewer") or "compliance-record",
            state=approval_state,
            decided_on=row.get("decided_on") or row.get("valid_from") or None,
            valid_from=row.get("valid_from") or None,
            valid_to=row.get("valid_to") or None,
            channels=channels,
            product_ids=[product_id] if product_id else [],
            product_version_ids=[pvid] if pvid and registry.find("product_versions", pvid) else [],
            formula_version_ids=[formula_id] if formula_id else [],
            note=row.get("note") or (f"源状态={raw_state}" if raw_state else None),
            source_state=raw_state or None,
        )

    # ---- 可选页面/物料清单 ---------------------------------------------
    pages_path = root / "pages.csv"
    if include_pages and pages_path.exists():
        for row in _read_csv(pages_path):
            asset_id = row.get("asset") or row["publication"]
            if not registry.find("assets", asset_id):
                service.register_asset(
                    asset_id,
                    row.get("name") or asset_id,
                    kind=row.get("kind") or "page",
                    location=row.get("location") or row.get("url") or None,
                    owner=row.get("owner") or None,
                )
            campaign_id = row.get("campaign") or None
            if campaign_id and not registry.find("campaigns", campaign_id):
                service.create_campaign(
                    campaign_id,
                    row.get("campaign_name") or campaign_id,
                    started_on=row.get("campaign_started") or row.get("published_on"),
                    ended_on=row.get("campaign_ended") or None,
                    owner=row.get("campaign_owner") or None,
                )
            product_id = row.get("product") or None
            formula_id = row.get("formula") or None
            pvid = row.get("product_version") or (
                f"PV:{product_id}:{formula_id}" if product_id and formula_id else None
            )
            approval_id = row.get("approval") or (f"AP-{row['claim']}" if row.get("claim") else None)
            service.import_legacy_publication(
                row["publication"],
                asset_id,
                row["claim"],
                channel=row.get("channel") or "unassigned",
                published_on=row["published_on"],
                approval_id=approval_id if approval_id and registry.find("approvals", approval_id) else None,
                product_id=product_id,
                product_version_id=pvid if pvid and registry.find("product_versions", pvid) else None,
                formula_version_id=formula_id or None,
                campaign_id=campaign_id,
                state=row.get("status") or "active",
                ended_on=row.get("ended_on") or None,
                source=row.get("source") or "pages.csv",
            )

    return registry
