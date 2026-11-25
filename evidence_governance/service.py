"""应用服务层：登记、审批、发布、影响扫描和历史还原。"""

from __future__ import annotations

import copy
import hashlib
from datetime import date, timedelta
from typing import Any

from .evaluation import current_product_version, evaluate_claim_use
from .models import (
    Approval,
    ApprovalScope,
    Applicability,
    Asset,
    AuditEntry,
    Campaign,
    Claim,
    ClaimTextVersion,
    EvidenceRef,
    Experiment,
    ExperimentVersion,
    FormulaComponent,
    FormulaVersion,
    Material,
    MaterialBatch,
    MaterialLotUse,
    Paper,
    Product,
    ProductVersion,
    Protocol,
    Publication,
    ReviewCase,
    SalesBatch,
)
from .store import Registry
from .utils import GovernanceError, date_within, parse_date


def _dicts_to_components(values: list[Any] | None) -> list[FormulaComponent]:
    result: list[FormulaComponent] = []
    for value in values or []:
        if isinstance(value, FormulaComponent):
            result.append(value)
        else:
            result.append(FormulaComponent(**value))
    return result


def _evidence_refs(values: list[Any] | None) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    for value in values or []:
        if isinstance(value, EvidenceRef):
            result.append(value)
        elif isinstance(value, dict):
            result.append(EvidenceRef(**value))
        else:
            raise GovernanceError("EvidenceRef 必须是 EvidenceRef 或 dict")
    return result


class GovernanceService:
    """供 CLI、测试或未来 Web/API 层调用的主用例集合。"""

    def __init__(self, registry: Registry | None = None):
        self.registry = registry or Registry()

    # ------------------------------------------------------------------
    # 基础登记
    # ------------------------------------------------------------------
    def audit(self, action: str, actor: str = "system", on: str | date | None = None, **details: Any) -> None:
        day = (parse_date(on) or date.today()).isoformat()
        self.registry.data.audit.append(AuditEntry(on=day, actor=actor, action=action, details=details))

    def register_paper(self, paper_id: str, title: str = "", **kwargs: Any) -> Paper:
        paper = Paper(id=paper_id, title=title, **kwargs)
        self.registry.add("papers", paper)
        return paper

    def register_protocol(
        self,
        protocol_id: str,
        title: str = "",
        *,
        protocol_version: str = "1",
        experiment_id: str | None = None,
        objective: str | None = None,
        method: str | None = None,
        concentration: str | None = None,
        condition: str | None = None,
        parameters: dict[str, Any] | None = None,
        owner: str | None = None,
        created_on: str | date | None = None,
        attach_to_experiment_version: int | None = None,
    ) -> Protocol:
        if experiment_id:
            experiment = self.registry.get("experiments", experiment_id)
        else:
            experiment = None
        protocol = Protocol(
            id=protocol_id,
            title=title,
            protocol_version=str(protocol_version),
            experiment_id=experiment_id,
            objective=objective,
            method=method,
            concentration=concentration,
            condition=condition,
            parameters=parameters or {},
            owner=owner,
            created_on=parse_date(created_on).isoformat() if created_on else None,
        )
        self.registry.add("protocols", protocol)
        if experiment is not None:
            version = experiment.get_version(attach_to_experiment_version)
            if version is None:
                raise GovernanceError(
                    f"实验 {experiment_id} 不存在版本 {attach_to_experiment_version or 'current'}，无法挂载方案"
                )
            version.protocol_id = protocol_id
            if not version.protocol and method:
                version.protocol = method
            self.registry.add("experiments", experiment, replace=True)
        return protocol

    def register_material(self, material_id: str, name: str = "", keywords: list[str] | None = None) -> Material:
        material = Material(id=material_id, name=name, keywords=keywords or [])
        self.registry.add("materials", material)
        return material

    def register_material_batch(
        self,
        batch_id: str,
        material_id: str,
        *,
        batch_code: str | None = None,
        received_on: str | None = None,
        note: str | None = None,
    ) -> MaterialBatch:
        self.registry.get("materials", material_id)
        batch = MaterialBatch(
            id=batch_id,
            material_id=material_id,
            batch_code=batch_code,
            received_on=received_on,
            note=note,
        )
        self.registry.add("material_batches", batch)
        return batch

    def register_experiment(
        self,
        experiment_id: str,
        *,
        title: str = "",
        paper_id: str | None = None,
        version: int = 1,
        recorded_on: str | None = None,
        material_id: str | None = None,
        concentration: str | None = None,
        condition: str | None = None,
        protocol: str | None = None,
        protocol_id: str | None = None,
        result: str | None = None,
        result_summary: str | None = None,
        conclusion_id: str | None = None,
        status: str = "active",
    ) -> Experiment:
        if paper_id:
            self.registry.get("papers", paper_id)
        if material_id:
            self.registry.get("materials", material_id)
        if protocol_id:
            self.registry.get("protocols", protocol_id)
        experiment_version = ExperimentVersion(
            version=version,
            recorded_on=recorded_on,
            protocol_id=protocol_id,
            material_id=material_id,
            concentration=concentration,
            condition=condition,
            protocol=protocol,
            result=result,
            result_summary=result_summary,
            conclusion_id=conclusion_id,
            status=status,
        )
        experiment = Experiment(
            id=experiment_id,
            title=title,
            paper_id=paper_id,
            versions=[experiment_version],
        )
        self.registry.add("experiments", experiment)
        return experiment

    def add_experiment_version(self, experiment_id: str, **kwargs: Any) -> ExperimentVersion:
        experiment = self.registry.get("experiments", experiment_id)
        next_version = max((item.version for item in experiment.versions), default=0) + 1
        requested = kwargs.pop("version", None) or next_version
        if experiment.get_version(requested):
            raise GovernanceError(f"实验 {experiment_id} v{requested} 已存在", code="duplicate_version")
        if kwargs.get("material_id"):
            self.registry.get("materials", kwargs["material_id"])
        version = ExperimentVersion(version=requested, **kwargs)
        experiment.versions.append(version)
        self.registry.add("experiments", experiment, replace=True)
        return version

    def overturn_experiment(
        self,
        old_experiment_id: str,
        *,
        new_experiment_id: str,
        old_version: int | None = None,
        new_version: int | None = None,
        reason: str,
        actor: str = "rd",
        on: str | date | None = None,
    ) -> ExperimentVersion:
        """显式登记“新实验推翻旧结论”。系统不会从 result 文本猜测推翻关系。"""
        old = self.registry.get("experiments", old_experiment_id)
        new = self.registry.get("experiments", new_experiment_id)
        target = old.get_version(old_version)
        if target is None:
            raise GovernanceError(
                f"旧实验 {old_experiment_id} v{old_version or 'current'} 不存在",
                code="not_found",
            )
        source = new.get_version(new_version)
        if source is None:
            raise GovernanceError(
                f"新实验 {new_experiment_id} v{new_version or 'current'} 不存在",
                code="not_found",
            )
        target.status = "superseded"
        target.overturned_by_experiment_id = new_experiment_id
        target.overturned_by_version = source.version
        target.overturn_reason = reason
        self.registry.add("experiments", old, replace=True)
        self.audit(
            "experiment_overturned",
            actor,
            on,
            old_experiment_id=old_experiment_id,
            old_version=target.version,
            new_experiment_id=new_experiment_id,
            new_version=source.version,
            reason=reason,
        )
        return target

    # ------------------------------------------------------------------
    # R&D 确认适用范围
    # ------------------------------------------------------------------
    def register_applicability(
        self,
        experiment_id: str,
        material_id: str,
        *,
        applicability_id: str | None = None,
        experiment_version: int | None = None,
        scope: str = "unspecified",
        material_batch_id: str | None = None,
        concentration: str | None = None,
        condition: str | None = None,
        confirmed_by: str | None = None,
        confirmed_on: str | date | None = None,
        note: str | None = None,
    ) -> Applicability:
        experiment = self.registry.get("experiments", experiment_id)
        version = experiment.get_version(experiment_version)
        if version is None:
            raise GovernanceError("必须为适用范围指定已存在的实验版本", code="not_found")
        self.registry.get("materials", material_id)
        if material_batch_id:
            batch = self.registry.get("material_batches", material_batch_id)
            if batch.material_id != material_id:
                raise GovernanceError("原料批次不属于指定原料", code="context_conflict")
        if version.material_id and version.material_id != material_id:
            raise GovernanceError(
                f"适用范围原料 {material_id} 与实验版本受测原料 {version.material_id} 不一致",
                code="context_conflict",
            )

        concentration = concentration if concentration is not None else version.concentration
        condition = condition if condition is not None else version.condition
        missing: list[str] = []
        if scope not in {"material_wide", "batch_specific", "unspecified"}:
            raise GovernanceError("scope 必须是 material_wide、batch_specific 或 unspecified")
        if scope == "unspecified":
            missing.append("batch_scope")
        if scope == "batch_specific" and not material_batch_id:
            missing.append("material_batch_id")
        if not concentration:
            missing.append("concentration")
        if not condition:
            missing.append("condition")
        if not confirmed_by:
            missing.append("rd_confirmation")

        state = "confirmed" if confirmed_by and not missing else "incomplete"
        app_id = applicability_id or self.registry.next_id("APP")
        applicability = Applicability(
            id=app_id,
            experiment_id=experiment_id,
            experiment_version=version.version,
            material_id=material_id,
            state=state,
            scope=scope,
            material_batch_id=material_batch_id,
            concentration=concentration,
            condition=condition,
            confirmed_by=confirmed_by if state == "confirmed" else confirmed_by,
            confirmed_on=(parse_date(confirmed_on) or date.today()).isoformat() if confirmed_by else None,
            missing=missing,
            note=note,
        )
        self.registry.add("applicabilities", applicability, replace=applicability_id is not None)
        return applicability

    def confirm_applicability(self, applicability_id: str, actor: str, on: str | date | None = None) -> Applicability:
        app = self.registry.get("applicabilities", applicability_id)
        # rd_confirmation 表示“尚未有人确认”，本次调用本身会补齐它；
        # 其他事实字段（批次、浓度、条件等）缺失时仍不能确认。
        remaining = [code for code in app.missing if code != "rd_confirmation"]
        if remaining:
            raise GovernanceError(
                f"适用范围仍缺少 {', '.join(remaining)}，不能标记为完整确认",
                code="incomplete_evidence",
                details={"missing": remaining},
            )
        app.missing = []
        app.state = "confirmed"
        app.confirmed_by = actor
        app.confirmed_on = (parse_date(on) or date.today()).isoformat()
        self.registry.add("applicabilities", app, replace=True)
        self.audit("applicability_confirmed", actor, on, applicability_id=applicability_id)
        return app

    # ------------------------------------------------------------------
    # 配方、商品版本、批次
    # ------------------------------------------------------------------
    def register_formula(
        self,
        formula_id: str,
        *,
        formula_code: str | None = None,
        version_label: str | None = None,
        parent_id: str | None = None,
        effective_from: str | None = None,
        effective_to: str | None = None,
        components: list[FormulaComponent | dict[str, Any]] | None = None,
        owner: str | None = None,
        owner_confirmed_on: str | date | None = None,
        change_summary: str | None = None,
        lineage_state: str = "unconfirmed",
    ) -> FormulaVersion:
        if parent_id:
            self.registry.find("formulas", parent_id)
        formula = FormulaVersion(
            id=formula_id,
            formula_code=formula_code,
            version_label=version_label,
            parent_id=parent_id,
            lineage_state=lineage_state,
            effective_from=effective_from,
            effective_to=effective_to,
            components=_dicts_to_components(components),
            owner=owner,
            owner_confirmed_on=(parse_date(owner_confirmed_on) or date.today()).isoformat()
            if owner_confirmed_on or owner
            else None,
            change_summary=change_summary,
        )
        self.registry.add("formulas", formula)
        return formula

    def confirm_formula_lineage(
        self,
        formula_id: str,
        parent_id: str,
        owner: str,
        on: str | date | None = None,
    ) -> FormulaVersion:
        formula = self.registry.get("formulas", formula_id)
        parent = self.registry.get("formulas", parent_id)
        if formula.id == parent.id:
            raise GovernanceError("配方不能继承自身")
        if formula.parent_id and formula.parent_id != parent_id:
            raise GovernanceError(
                f"配方 {formula_id} 已登记父版本 {formula.parent_id}，不能改为 {parent_id}",
                code="context_conflict",
            )
        formula.parent_id = parent_id
        formula.lineage_state = "confirmed"
        formula.lineage_confirmed_by = owner
        formula.lineage_confirmed_on = (parse_date(on) or date.today()).isoformat()
        self.registry.add("formulas", formula, replace=True)
        self.audit("formula_lineage_confirmed", owner, on, formula_id=formula_id, parent_id=parent_id)
        return formula

    def register_product(self, product_id: str, name: str = "", owner: str | None = None) -> Product:
        product = Product(id=product_id, name=name, owner=owner)
        self.registry.add("products", product)
        return product

    def register_product_version(
        self,
        product_version_id: str,
        product_id: str,
        formula_version_id: str,
        *,
        label: str | None = None,
        effective_from: str | None = None,
        effective_to: str | None = None,
        owner: str | None = None,
        owner_confirmed_on: str | date | None = None,
    ) -> ProductVersion:
        self.registry.get("products", product_id)
        self.registry.get("formulas", formula_version_id)
        pv = ProductVersion(
            id=product_version_id,
            product_id=product_id,
            formula_version_id=formula_version_id,
            label=label,
            effective_from=effective_from,
            effective_to=effective_to,
            owner=owner,
            owner_confirmed_on=(parse_date(owner_confirmed_on) or date.today()).isoformat()
            if owner or owner_confirmed_on
            else None,
        )
        self.registry.add("product_versions", pv)
        return pv

    def register_material_lot_use(
        self,
        product_version_id: str,
        material_id: str,
        material_batch_id: str | None = None,
        *,
        use_id: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> MaterialLotUse:
        self.registry.get("product_versions", product_version_id)
        self.registry.get("materials", material_id)
        if material_batch_id:
            batch = self.registry.get("material_batches", material_batch_id)
            if batch.material_id != material_id:
                raise GovernanceError("原料批次不属于指定原料")
        use_id = use_id or self.registry.next_id("LOTUSE")
        use = MaterialLotUse(
            id=use_id,
            product_version_id=product_version_id,
            material_id=material_id,
            material_batch_id=material_batch_id,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        self.registry.add("material_lot_uses", use)
        return use

    def register_sales_batch(
        self,
        sales_batch_id: str,
        product_id: str,
        product_version_id: str,
        *,
        batch_code: str | None = None,
        produced_on: str | None = None,
        sell_from: str | None = None,
        sell_to: str | None = None,
        parent_sales_batch_id: str | None = None,
        owner: str | None = None,
    ) -> SalesBatch:
        self.registry.get("products", product_id)
        pv = self.registry.get("product_versions", product_version_id)
        if pv.product_id != product_id:
            raise GovernanceError("商品版本不属于该商品")
        if parent_sales_batch_id:
            self.registry.get("sales_batches", parent_sales_batch_id)
        batch = SalesBatch(
            id=sales_batch_id,
            product_id=product_id,
            product_version_id=product_version_id,
            batch_code=batch_code,
            produced_on=produced_on,
            sell_from=sell_from,
            sell_to=sell_to,
            parent_sales_batch_id=parent_sales_batch_id,
            owner=owner,
        )
        self.registry.add("sales_batches", batch)
        return batch

    def confirm_sales_batch_inheritance(
        self,
        sales_batch_id: str,
        owner: str,
        on: str | date | None = None,
        *,
        parent_sales_batch_id: str | None = None,
    ) -> SalesBatch:
        batch = self.registry.get("sales_batches", sales_batch_id)
        if parent_sales_batch_id:
            parent = self.registry.get("sales_batches", parent_sales_batch_id)
            if parent.product_id != batch.product_id:
                raise GovernanceError("继承关系必须属于同一商品")
            batch.parent_sales_batch_id = parent_sales_batch_id
        batch.inheritance_state = "confirmed"
        batch.inheritance_confirmed_by = owner
        batch.inheritance_confirmed_on = (parse_date(on) or date.today()).isoformat()
        self.registry.add("sales_batches", batch, replace=True)
        self.audit("sales_batch_inheritance_confirmed", owner, on, sales_batch_id=sales_batch_id)
        return batch

    # ------------------------------------------------------------------
    # 表述、证据引用、合规审批
    # ------------------------------------------------------------------
    def create_claim(
        self,
        claim_id: str,
        text: str,
        *,
        owner: str | None = None,
        changed_by: str | None = None,
        on: str | date | None = None,
        evidence_links: list[EvidenceRef | dict[str, Any]] | None = None,
    ) -> Claim:
        day = parse_date(on).isoformat() if on else None
        claim = Claim(
            id=claim_id,
            owner=owner,
            current_revision=1,
            text_versions=[ClaimTextVersion(revision=1, text=text, changed_by=changed_by, changed_on=day)],
            evidence_links=_evidence_refs(evidence_links),
        )
        self.registry.add("claims", claim)
        return claim

    def revise_claim_text(
        self,
        claim_id: str,
        text: str,
        *,
        changed_by: str,
        on: str | date | None = None,
        reason: str | None = None,
    ) -> Claim:
        claim = self.registry.get("claims", claim_id)
        day = (parse_date(on) or date.today()).isoformat()
        next_revision = claim.current_revision + 1
        claim.text_versions.append(
            ClaimTextVersion(
                revision=next_revision,
                text=text,
                changed_by=changed_by,
                changed_on=day,
                reason=reason,
            )
        )
        claim.current_revision = next_revision
        self.registry.add("claims", claim, replace=True)
        return claim

    def link_claim_evidence(
        self,
        claim_id: str,
        experiment_id: str,
        experiment_version: int,
        *,
        stance: str = "supports",
        applicability_id: str | None = None,
        version_explicit: bool = True,
        note: str | None = None,
    ) -> Claim:
        claim = self.registry.get("claims", claim_id)
        experiment = self.registry.get("experiments", experiment_id)
        if not experiment.get_version(experiment_version):
            raise GovernanceError(f"实验版本 {experiment_id} v{experiment_version} 不存在")
        if applicability_id:
            app = self.registry.get("applicabilities", applicability_id)
            if app.experiment_id != experiment_id or app.experiment_version != experiment_version:
                raise GovernanceError("applicability_id 与实验版本不匹配")
        claim.evidence_links.append(
            EvidenceRef(
                experiment_id=experiment_id,
                experiment_version=experiment_version,
                stance=stance,
                applicability_id=applicability_id,
                version_explicit=version_explicit,
                note=note,
            )
        )
        self.registry.add("claims", claim, replace=True)
        return claim

    def approve_claim(
        self,
        approval_id: str,
        claim_id: str,
        reviewer: str,
        *,
        claim_revision: int | None = None,
        decided_on: str | date | None = None,
        valid_from: str | date | None = None,
        valid_to: str | date | None = None,
        channels: list[str] | None = None,
        product_ids: list[str] | None = None,
        product_version_ids: list[str] | None = None,
        formula_version_ids: list[str] | None = None,
        sales_batch_ids: list[str] | None = None,
        all_product_versions: bool = False,
        scope: ApprovalScope | dict[str, Any] | None = None,
        evidence_refs: list[EvidenceRef | dict[str, Any]] | None = None,
        state: str = "approved",
        note: str | None = None,
        source_state: str | None = None,
    ) -> Approval:
        claim = self.registry.get("claims", claim_id)
        revision = claim_revision or claim.current_revision
        if not claim.get_text_version(revision):
            raise GovernanceError(f"表述 {claim_id} v{revision} 不存在")
        if scope is None:
            scope_obj = ApprovalScope(
                product_ids=product_ids or [],
                product_version_ids=product_version_ids or [],
                formula_version_ids=formula_version_ids or [],
                sales_batch_ids=sales_batch_ids or [],
                all_product_versions=all_product_versions,
            )
        elif isinstance(scope, ApprovalScope):
            scope_obj = scope
        else:
            scope_obj = ApprovalScope.from_dict(scope)

        frozen_refs = _evidence_refs(evidence_refs) if evidence_refs is not None else copy.deepcopy(claim.evidence_links)
        for ref in frozen_refs:
            # 不存在的实验/版本不在登记审批时硬拒绝：来源缺失本身要能入库并由评估器
            # 标成 evidence_version_missing；这里只拦截已经能判定的交叉引用错误。
            experiment = self.registry.find("experiments", ref.experiment_id)
            if experiment is not None and ref.applicability_id:
                applicability = self.registry.find("applicabilities", ref.applicability_id)
                if applicability is None:
                    raise GovernanceError(
                        f"applicability_id {ref.applicability_id} 未登记",
                        code="not_found",
                    )
                if (
                    applicability.experiment_id != ref.experiment_id
                    or applicability.experiment_version != ref.experiment_version
                ):
                    raise GovernanceError(
                        "applicability_id 与审批证据的实验/版本不匹配",
                        code="context_conflict",
                    )
                version = experiment.get_version(ref.experiment_version)
                if (
                    version
                    and version.material_id
                    and applicability.material_id
                    and version.material_id != applicability.material_id
                ):
                    raise GovernanceError(
                        "applicability_id 的原料与实验版本受测原料不匹配",
                        code="context_conflict",
                    )
        approval = Approval(
            id=approval_id,
            claim_id=claim_id,
            claim_revision=revision,
            reviewer=reviewer,
            state=state,
            decided_on=parse_date(decided_on).isoformat() if decided_on else None,
            valid_from=parse_date(valid_from).isoformat() if parse_date(valid_from) else None,
            valid_to=parse_date(valid_to).isoformat() if parse_date(valid_to) else None,
            channels=channels or [],
            scope=scope_obj,
            evidence_refs=frozen_refs,
            note=note,
            source_state=source_state,
        )
        self.registry.add("approvals", approval, replace=True)
        self.audit("claim_approval_recorded", reviewer, decided_on, approval_id=approval_id, claim_id=claim_id, state=state)
        return approval

    def revoke_approval(
        self,
        approval_id: str,
        reviewer: str,
        *,
        on: str | date | None = None,
        note: str | None = None,
    ) -> Approval:
        approval = self.registry.get("approvals", approval_id)
        approval.state = "revoked"
        approval.note = note or approval.note
        self.registry.add("approvals", approval, replace=True)
        self.audit("approval_revoked", reviewer, on, approval_id=approval_id)
        return approval

    def claim_usage_guide(self, claim_id: str, on_date: str | date | None = None) -> dict[str, Any]:
        claim = self.registry.get("claims", claim_id)
        day = (parse_date(on_date) or date.today()).isoformat()
        approvals = []
        for approval in self.registry.data.approvals:
            if approval.claim_id != claim_id:
                continue
            start = parse_date(approval.valid_from)
            end = parse_date(approval.valid_to)
            current = approval.state == "approved" and (
                (start is None or parse_date(day) >= start) and (end is None or parse_date(day) <= end)
            )
            expanded_versions = []
            for pvid in approval.scope.product_version_ids:
                pv = self.registry.find("product_versions", pvid)
                if pv:
                    expanded_versions.append(pv.to_dict())
            formulas = [
                self.registry.find("formulas", fid).to_dict()
                for fid in approval.scope.formula_version_ids
                if self.registry.find("formulas", fid)
            ]
            batches = [
                self.registry.find("sales_batches", bid).to_dict()
                for bid in approval.scope.sales_batch_ids
                if self.registry.find("sales_batches", bid)
            ]
            approvals.append(
                {
                    "approval_id": approval.id,
                    "reviewer": approval.reviewer,
                    "state": approval.state,
                    "currently_effective": current,
                    "channels": approval.channels,
                    "valid_from": approval.valid_from,
                    "valid_to": approval.valid_to,
                    "scope": approval.scope.to_dict(),
                    "product_versions": expanded_versions,
                    "formula_versions": formulas,
                    "sales_batches": batches,
                    "evidence_refs": [ref.to_dict() for ref in approval.evidence_refs],
                    "note": approval.note,
                }
            )
        return {
            "claim_id": claim.id,
            "current_revision": claim.current_revision,
            "text": claim.text,
            "text_versions": [item.to_dict() for item in claim.text_versions],
            "registered_evidence_links": [ref.to_dict() for ref in claim.evidence_links],
            "approvals": approvals,
            "on_date": day,
        }

    # ------------------------------------------------------------------
    # 资产、活动、发布和不可变快照
    # ------------------------------------------------------------------
    def register_asset(
        self,
        asset_id: str,
        name: str = "",
        *,
        kind: str = "page",
        location: str | None = None,
        owner: str | None = None,
    ) -> Asset:
        asset = Asset(id=asset_id, name=name, kind=kind, location=location, owner=owner)
        self.registry.add("assets", asset)
        return asset

    def create_campaign(
        self,
        campaign_id: str,
        name: str = "",
        *,
        started_on: str | date | None = None,
        ended_on: str | date | None = None,
        owner: str | None = None,
        description: str | None = None,
    ) -> Campaign:
        campaign = Campaign(
            id=campaign_id,
            name=name,
            started_on=parse_date(started_on).isoformat() if parse_date(started_on) else None,
            ended_on=parse_date(ended_on).isoformat() if parse_date(ended_on) else None,
            owner=owner,
            description=description,
        )
        self.registry.add("campaigns", campaign)
        return campaign

    def end_campaign(
        self,
        campaign_id: str,
        ended_on: str | date,
        *,
        actor: str = "marketing",
        end_publications: bool = True,
    ) -> Campaign:
        campaign = self.registry.get("campaigns", campaign_id)
        day = parse_date(ended_on).isoformat()
        campaign.ended_on = day
        self.registry.add("campaigns", campaign, replace=True)
        ended: list[str] = []
        if end_publications:
            for pub in self.registry.data.publications:
                if pub.campaign_id == campaign_id and pub.state == "active":
                    pub.state = "ended"
                    pub.ended_on = day
                    ended.append(pub.id)
        self.audit("campaign_ended", actor, day, campaign_id=campaign_id, publications=ended)
        return campaign

    def _publication_context_kwargs(self, pub: Publication) -> dict[str, Any]:
        return {
            "channel": pub.channel,
            "product_id": pub.product_id,
            "product_version_id": pub.product_version_id,
            "formula_version_id": pub.formula_version_id,
            "sales_batch_id": pub.sales_batch_id,
        }

    def _build_snapshot(self, pub_id: str, report: dict[str, Any], captured_on: str) -> dict[str, Any]:
        approval = report.get("approval")
        claim = self.registry.get("claims", report["claim_id"])
        revision = report.get("claim_revision") or claim.current_revision
        text_version = claim.get_text_version(revision)
        evidence_details = []
        for item in report.get("evidence", []):
            evidence_details.append(copy.deepcopy(item))
        return {
            "schema": "evidence-publication-snapshot/v1",
            "publication_id": pub_id,
            "captured_on": captured_on,
            "claim": {
                "id": claim.id,
                "revision": revision,
                "text": text_version.text if text_version else report.get("text"),
            },
            "approval": copy.deepcopy(approval),
            "context": copy.deepcopy(report.get("context")),
            "evidence_at_publication": evidence_details,
            "decision_at_publication": {
                "usable": report.get("usable"),
                "approved_for_scope": report.get("approved_for_scope"),
                "evidence_verified": report.get("evidence_verified"),
                "blockers": copy.deepcopy(report.get("blockers", [])),
                "missing_evidence": copy.deepcopy(report.get("missing_evidence", [])),
            },
        }

    def publish_claim(
        self,
        publication_id: str,
        asset_id: str,
        claim_id: str,
        *,
        channel: str,
        published_on: str | date,
        approval_id: str | None = None,
        product_id: str | None = None,
        product_version_id: str | None = None,
        formula_version_id: str | None = None,
        sales_batch_id: str | None = None,
        campaign_id: str | None = None,
        actor: str = "marketing",
    ) -> Publication:
        self.registry.get("assets", asset_id)
        if campaign_id:
            self.registry.get("campaigns", campaign_id)
        day = parse_date(published_on).isoformat()
        report = evaluate_claim_use(
            self.registry,
            claim_id,
            day,
            approval_id=approval_id,
            channel=channel,
            product_id=product_id,
            product_version_id=product_version_id,
            formula_version_id=formula_version_id,
            sales_batch_id=sales_batch_id,
        )
        if not report["usable"]:
            raise GovernanceError(
                "表述在发布日期未同时满足有效审批、范围一致和证据完整条件",
                code="publication_not_authorized",
                details={"blockers": report["blockers"], "missing_evidence": report["missing_evidence"]},
            )
        effective_approval_id = report["approval"]["id"]
        approval = self.registry.get("approvals", effective_approval_id)
        pub = Publication(
            id=publication_id,
            asset_id=asset_id,
            claim_id=claim_id,
            approval_id=effective_approval_id,
            channel=channel,
            published_on=day,
            claim_revision=approval.claim_revision,
            product_id=report["context"]["product_id"] or product_id,
            product_version_id=product_version_id or report["context"]["product_version_id"],
            formula_version_id=formula_version_id or report["context"]["formula_version_id"],
            sales_batch_id=sales_batch_id or report["context"]["sales_batch_id"],
            campaign_id=campaign_id,
            state="active",
        )
        pub.snapshot = self._build_snapshot(publication_id, report, day)
        drift_issues = self._formula_drift_issues(pub, parse_date(day))
        if drift_issues:
            raise GovernanceError(
                "发布上下文存在尚未复核的商品/配方版本漂移；已有旧页面请用 import_legacy_publication 登记后走复核流程",
                code="publication_version_drift",
                details={"drift": drift_issues},
            )
        self.registry.add("publications", pub)
        self.audit("claim_published", actor, day, publication_id=publication_id, claim_id=claim_id)
        return pub

    def import_legacy_publication(
        self,
        publication_id: str,
        asset_id: str,
        claim_id: str,
        *,
        channel: str,
        published_on: str | date,
        approval_id: str | None = None,
        product_id: str | None = None,
        product_version_id: str | None = None,
        formula_version_id: str | None = None,
        sales_batch_id: str | None = None,
        campaign_id: str | None = None,
        state: str = "active",
        ended_on: str | date | None = None,
        source: str | None = "legacy_inventory_import",
    ) -> Publication:
        """登记系统建设前已经存在的页面/物料。

        与正常发布不同，此方法不把历史导入误当成“当时已验证”；快照会如实保存
        找到的审批和所有无法确认的问题，随后由复核扫描处理。
        """
        self.registry.find("assets", asset_id) or self.register_asset(asset_id, asset_id, kind="unknown")
        day = parse_date(published_on).isoformat()
        report = evaluate_claim_use(
            self.registry,
            claim_id,
            day,
            approval_id=approval_id or None,
            channel=channel,
            product_id=product_id,
            product_version_id=product_version_id,
            formula_version_id=formula_version_id,
            sales_batch_id=sales_batch_id,
        )
        effective_approval_id = approval_id or (report.get("approval") or {}).get("id") or ""
        revision = (report.get("approval") or {}).get("claim_revision") or report.get("claim_revision")
        pub = Publication(
            id=publication_id,
            asset_id=asset_id,
            claim_id=claim_id,
            approval_id=effective_approval_id,
            channel=channel,
            published_on=day,
            claim_revision=revision,
            product_id=report["context"]["product_id"] or product_id,
            product_version_id=product_version_id or report["context"]["product_version_id"],
            formula_version_id=formula_version_id or report["context"]["formula_version_id"],
            sales_batch_id=sales_batch_id or report["context"]["sales_batch_id"],
            campaign_id=campaign_id,
            state=state,
            ended_on=parse_date(ended_on).isoformat() if ended_on else None,
            source=source,
        )
        pub.snapshot = self._build_snapshot(publication_id, report, day)
        pub.snapshot["legacy_import"] = True
        self.registry.add("publications", pub, replace=True)
        return pub

    def end_publication(
        self,
        publication_id: str,
        on: str | date,
        *,
        removed: bool = False,
        actor: str = "product_owner",
        reason: str | None = None,
    ) -> Publication:
        pub = self.registry.get("publications", publication_id)
        day = parse_date(on).isoformat()
        pub.state = "removed" if removed else "ended"
        pub.ended_on = day
        self.registry.add("publications", pub, replace=True)
        self.audit(
            "publication_removed" if removed else "publication_ended",
            actor,
            day,
            publication_id=publication_id,
            reason=reason,
        )
        return pub

    def replace_publication(
        self,
        old_publication_id: str,
        new_publication_id: str,
        on: str | date,
        *,
        actor: str = "product_owner",
    ) -> Publication:
        old = self.registry.get("publications", old_publication_id)
        new = self.registry.get("publications", new_publication_id)
        day = parse_date(on).isoformat()
        old.state = "ended"
        old.ended_on = day
        old.replaced_by_publication_id = new_publication_id
        self.registry.add("publications", old, replace=True)
        self.audit(
            "publication_replaced",
            actor,
            day,
            old_publication_id=old_publication_id,
            new_publication_id=new_publication_id,
        )
        return old

    # ------------------------------------------------------------------
    # 影响扫描与复核案件
    # ------------------------------------------------------------------
    @staticmethod
    def _case_id(key: str) -> str:
        return "RC-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

    def publication_is_active(self, pub: Publication, on_date: date | str) -> bool:
        day = parse_date(on_date) if isinstance(on_date, str) else on_date
        if pub.state in {"ended", "removed"}:
            return False
        if pub.ended_on:
            end = parse_date(pub.ended_on)
            if end and day > end:
                return False
        return True

    def _formula_drift_issues(self, pub: Publication, day: date) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        current_new_version = False
        current_pv = None
        if pub.product_id and pub.product_version_id:
            current_pv = current_product_version(self.registry, pub.product_id, day)
            if current_pv and current_pv.id != pub.product_version_id:
                current_new_version = True
                issues.append(
                    {
                        "code": "formula_changed",
                        "severity": "blocker",  # 对“当前商品有效展示”是阻断；复核确认仅保留旧版本后可按 legacy 处理
                        "subject_type": "product_version",
                        "subject_id": current_pv.id,
                        "message": (
                            f"页面引用旧商品版本 {pub.product_version_id}/配方 {pub.formula_version_id}；"
                            f"商品当前版本为 {current_pv.id}/配方 {current_pv.formula_version_id}，"
                            "旧证据和旧审批不自动覆盖新配方"
                        ),
                    }
                )
            elif not current_pv:
                old_pv = self.registry.find("product_versions", pub.product_version_id)
                if old_pv and not date_within(day, parse_date(old_pv.effective_from), parse_date(old_pv.effective_to)):
                    issues.append(
                        {
                            "code": "product_version_window_ended",
                            "severity": "warning",
                            "subject_type": "product_version",
                            "subject_id": old_pv.id,
                            "message": (
                                f"商品版本 {old_pv.id} 的生效/销售窗口已经结束，且没有登记中的后继当前版本；"
                                "必须确认该页面是否仍对应流通旧批次"
                            ),
                        }
                    )

        if pub.formula_version_id:
            children = []
            for formula in self.registry.data.formulas:
                if formula.parent_id != pub.formula_version_id:
                    continue
                child_start = parse_date(formula.effective_from)
                # 回溯历史发布时，未来才生效的配方版本不能作为当时的后继漂移。
                if child_start and child_start > day:
                    continue
                children.append(formula)
            for child in children:
                # 已存在更直接的“商品当前版本已切换”案件时，不再重复发一条
                # formula_version_superseded；但继承未确认仍需单独标出。
                if not current_new_version:
                    issues.append(
                        {
                            "code": "formula_version_superseded",
                            "severity": "warning",
                            "subject_type": "formula_version",
                            "subject_id": child.id,
                            "message": f"配方 {pub.formula_version_id} 已有后继版本 {child.id}，需确认该物料是否仍用于旧批次",
                        }
                    )
                if child.lineage_state != "confirmed":
                    issues.append(
                        {
                            "code": "formula_lineage_unconfirmed",
                            "severity": "gap",
                            "subject_type": "formula_version",
                            "subject_id": child.id,
                            "message": f"配方继承 {pub.formula_version_id} -> {child.id} 尚未由产品负责人确认",
                        }
                    )
        return issues

    def _dismissed_legacy(self, key: str) -> bool:
        case_id = self._case_id(key)
        case = self.registry.find("review_cases", case_id)
        return bool(
            case
            and case.status == "resolved"
            and case.resolution
            and case.resolution.get("action") == "legacy_confirmed"
            and case.code in {"formula_changed", "formula_version_superseded", "product_version_window_ended"}
        )

    def publication_display_state(
        self, publication_id: str, on_date: str | date | None = None
    ) -> dict[str, Any]:
        pub = self.registry.get("publications", publication_id)
        day = parse_date(on_date) or date.today()
        if pub.state == "removed":
            display_state = "removed"
        elif not self.publication_is_active(pub, day):
            display_state = "ended"
        else:
            report = evaluate_claim_use(
                self.registry,
                pub.claim_id,
                day,
                approval_id=pub.approval_id or None,
                **self._publication_context_kwargs(pub),
            )
            drift = self._formula_drift_issues(pub, day)
            unresolved_drift = []
            for issue in drift:
                key = "|".join([publication_id, issue["code"], issue.get("subject_id") or ""])
                if not self._dismissed_legacy(key):
                    unresolved_drift.append(issue)
            open_cases = [
                case.to_dict()
                for case in self.registry.data.review_cases
                if case.publication_id == publication_id and case.status == "open"
            ]
            attention_codes = {
                "formula_version_superseded",
                "formula_lineage_unconfirmed",
                "product_version_window_ended",
            }
            attention_cases = [
                case
                for case in open_cases
                if case.get("severity") in {"blocker", "gap"} or case.get("code") in attention_codes
            ]
            if report["blockers"]:
                display_state = "blocked"
            elif report["missing_evidence"] or not report["evidence_verified"]:
                # 来源/版本/适用范围不完整时不能给出“当前有效”，但它与已经证实的
                # 到期、越权或原料冲突不同，状态标为待补证据/待复核。
                display_state = "needs_review"
            elif unresolved_drift or attention_cases:
                display_state = "needs_review"
            else:
                # 仅有 approval_expiring_soon 等预警案件时，当天仍可显示为 valid；
                # 预警本身仍保留在 open_cases/看板中，不是静默忽略。
                display_state = "valid"
            return {
                "publication_id": publication_id,
                "display_state": display_state,
                "currently_valid": display_state == "valid",
                "evaluation": report,
                "formula_drift": drift,
                "unresolved_formula_drift": unresolved_drift,
                "open_cases": open_cases,
                "attention_cases": attention_cases,
                "on_date": day.isoformat(),
            }

        return {
            "publication_id": publication_id,
            "display_state": display_state,
            "currently_valid": False,
            "evaluation": None,
            "formula_drift": [],
            "unresolved_formula_drift": [],
            "open_cases": [],
            "attention_cases": [],
            "on_date": day.isoformat(),
        }

    def scan_for_review(
        self,
        on_date: str | date | None = None,
        *,
        expiring_soon_days: int = 30,
    ) -> list[ReviewCase]:
        day = parse_date(on_date) or date.today()
        day_iso = day.isoformat()
        current_keys: set[str] = set()
        open_case_ids: list[str] = []

        for pub in self.registry.data.publications:
            if not self.publication_is_active(pub, day):
                continue
            report = evaluate_claim_use(
                self.registry,
                pub.claim_id,
                day_iso,
                approval_id=pub.approval_id or None,
                **self._publication_context_kwargs(pub),
            )
            issue_dicts = list(report["issues"])
            issue_dicts.extend(self._formula_drift_issues(pub, day))

            # 即将到期不是当前无效，但提前生成复核/续期任务。
            if pub.approval_id:
                approval = self.registry.find("approvals", pub.approval_id)
                if approval and approval.state == "approved" and approval.valid_to:
                    end = parse_date(approval.valid_to)
                    if end and day <= end <= day + timedelta(days=expiring_soon_days):
                        issue_dicts.append(
                            {
                                "code": "approval_expiring_soon",
                                "severity": "warning",
                                "subject_type": "approval",
                                "subject_id": approval.id,
                                "message": f"审批 {approval.id} 将于 {approval.valid_to} 到期",
                            }
                        )

            for issue in issue_dicts:
                # 上下文冲突和 no_approval 等也必须进入复核列表。
                subject_id = issue.get("subject_id") or ""
                key = "|".join([pub.id, issue["code"], str(subject_id)])
                current_keys.add(key)
                case_id = self._case_id(key)
                title = self._title_for_code(issue["code"])
                existing = self.registry.find("review_cases", case_id)
                if existing is None:
                    case = ReviewCase(
                        id=case_id,
                        key=key,
                        code=issue["code"],
                        severity=issue.get("severity", "blocker"),
                        publication_id=pub.id,
                        title=title,
                        message=issue["message"],
                        subject_type=issue.get("subject_type"),
                        subject_id=subject_id or None,
                        status="open",
                        first_seen_on=day_iso,
                        last_seen_on=day_iso,
                    )
                    self.registry.add("review_cases", case)
                    open_case_ids.append(case_id)
                else:
                    existing.last_seen_on = day_iso
                    existing.message = issue["message"]
                    existing.severity = issue.get("severity", existing.severity)
                    if existing.status == "resolved":
                        resolution = existing.resolution or {}
                        legacy_dismissed = (
                            resolution.get("action") == "legacy_confirmed"
                            and existing.code in {
                                "formula_changed",
                                "formula_version_superseded",
                                "product_version_window_ended",
                            }
                        )
                        if not legacy_dismissed:
                            existing.status = "open"
                            existing.reopen_count += 1
                            existing.history.append(
                                {"on": day_iso, "action": "reopened", "reason": "同一问题在扫描中仍然存在"}
                            )
                    if existing.status == "open":
                        open_case_ids.append(existing.id)
                    self.registry.add("review_cases", existing, replace=True)

        # 自动关闭已经消失或随页面下线的旧问题。
        for case in list(self.registry.data.review_cases):
            if case.status != "open":
                continue
            # key 不含日期；若本轮没有出现，说明发布物已结束或问题已修复。
            if case.key not in current_keys:
                case.status = "resolved"
                case.resolution = {
                    "action": "auto_closed",
                    "by": "system",
                    "on": day_iso,
                    "note": "扫描时该问题已不存在，或对应页面/物料已停止使用",
                }
                self.registry.add("review_cases", case, replace=True)
            elif case.id in open_case_ids and case.status == "open":
                pass

        return [self.registry.review_cases[cid] for cid in open_case_ids if cid in self.registry.review_cases]

    @staticmethod
    def _title_for_code(code: str) -> str:
        titles = {
            "approval_expired": "审批已到期",
            "approval_revoked": "审批已撤销",
            "no_approval": "缺少有效合规审批",
            "claim_revision_changed": "表述版本超出已批准版本",
            "evidence_overturned": "证据已被新实验推翻或替代",
            "material_scope_mismatch": "实验原料与商品配方不匹配",
            "material_batch_mismatch": "实验原料批次不匹配",
            "formula_changed": "配方/商品版本已升级",
            "formula_version_superseded": "配方版本已有后继版本",
            "formula_lineage_unconfirmed": "配方继承关系待产品负责人确认",
            "sales_batch_inheritance_unconfirmed": "销售批次继承待确认",
            "applicability_unconfirmed": "实验适用条件未经 R&D 完整确认",
            "batch_applicability_unspecified": "缺少原料批次适用范围",
            "formula_concentration_missing": "配方浓度缺失，无法核对适用浓度",
            "approval_expiring_soon": "审批即将到期",
        }
        return titles.get(code, code.replace("_", " "))

    def list_open_review_cases(self) -> list[ReviewCase]:
        return [case for case in self.registry.data.review_cases if case.status == "open"]

    def publications_using_claim(
        self,
        claim_id: str,
        on_date: str | date | None = None,
        *,
        active_only: bool = False,
        with_state: bool = True,
    ) -> list[dict[str, Any]]:
        """列出引用某表述的页面/物料；影响事件发生后可用它定位仍在使用的资产。"""
        day = parse_date(on_date) or date.today()
        results = []
        for pub in self.registry.data.publications:
            if pub.claim_id != claim_id:
                continue
            is_active = self.publication_is_active(pub, day)
            if active_only and not is_active:
                continue
            asset = self.registry.find("assets", pub.asset_id)
            item: dict[str, Any] = {
                "publication": pub.to_dict(),
                "asset": asset.to_dict() if asset else None,
                "active": is_active,
            }
            if with_state:
                item["display"] = self.publication_display_state(pub.id, day)
            results.append(item)
        return results

    def impact_analysis(self, on_date: str | date | None = None, **kwargs: Any) -> dict[str, Any]:
        """review_dashboard 的业务别名。"""
        return self.review_dashboard(on_date, **kwargs)

    def review_dashboard(self, on_date: str | date | None = None) -> dict[str, Any]:
        day = parse_date(on_date) or date.today()
        cases = self.scan_for_review(day)
        publications = []
        for pub in self.registry.data.publications:
            state = self.publication_display_state(pub.id, day)
            asset = self.registry.find("assets", pub.asset_id)
            publications.append(
                {
                    "publication_id": pub.id,
                    "asset_id": pub.asset_id,
                    "asset_name": asset.name if asset else None,
                    "asset_kind": asset.kind if asset else None,
                    "location": asset.location if asset else None,
                    "campaign_id": pub.campaign_id,
                    "channel": pub.channel,
                    "claim_id": pub.claim_id,
                    "approval_id": pub.approval_id or None,
                    "display_state": state["display_state"],
                    "currently_valid": state["currently_valid"],
                    "published_on": pub.published_on,
                    "ended_on": pub.ended_on,
                    "open_case_ids": [case["id"] for case in state["open_cases"]],
                }
            )
        in_use = [item for item in publications if item["display_state"] in {"valid", "needs_review", "blocked"}]
        attention_ids = [
            item["publication_id"]
            for item in in_use
            if item["display_state"] in {"blocked", "needs_review"}
        ]
        return {
            "on_date": day.isoformat(),
            "open_case_count": len(cases),
            "open_cases": [case.to_dict() for case in cases],
            "review_items": [case.to_dict() for case in cases],
            "publications": publications,
            "in_use_publications": in_use,
            "affected_publications": [item for item in publications if item["publication_id"] in attention_ids],
            "blocked_or_review_publication_ids": attention_ids,
            "affected_publication_ids": attention_ids,
        }

    def resolve_review_case(
        self,
        case_id: str,
        reviewer: str,
        action: str,
        note: str,
        on: str | date | None = None,
        *,
        replacement_publication_id: str | None = None,
    ) -> ReviewCase:
        case = self.registry.get("review_cases", case_id)
        day = parse_date(on) or date.today()
        if case.status == "resolved":
            return case
        if not case.publication_id:
            raise GovernanceError("案件没有关联发布物，不能按页面复核流程处理")

        if action in {"removed", "take_down"}:
            self.end_publication(case.publication_id, day, removed=True, actor=reviewer, reason=note)
        elif action == "ended":
            self.end_publication(case.publication_id, day, removed=False, actor=reviewer, reason=note)
        elif action == "replaced":
            if not replacement_publication_id:
                raise GovernanceError("replaced 动作必须提供 replacement_publication_id")
            replacement_state = self.publication_display_state(replacement_publication_id, day)
            if replacement_state["display_state"] != "valid":
                raise GovernanceError(
                    "替代页面当前不是 valid 状态，不能关闭旧页面案件",
                    details={"state": replacement_state["display_state"]},
                )
            self.replace_publication(case.publication_id, replacement_publication_id, day, actor=reviewer)
        elif action == "legacy_confirmed":
            if case.code not in {
                "formula_changed",
                "formula_version_superseded",
                "product_version_window_ended",
            }:
                raise GovernanceError("legacy_confirmed 只能用于配方升级/旧版本窗口结束后的旧批次保留复核")
            state = self.publication_display_state(case.publication_id, day)
            evaluation = state.get("evaluation") or {}
            if not evaluation or not evaluation.get("legacy_scope_usable", evaluation.get("usable")):
                raise GovernanceError(
                    "旧版本表述本身仍未满足审批/证据条件，不能仅以继承关系确认结案",
                    details={"evaluation": evaluation},
                )
        elif action in {"evidence_supplemented", "scope_corrected", "reviewed_no_change"}:
            # 允许记录负责人判断；若扫描发现同一硬问题仍在，会自动重新打开。
            pass
        else:
            raise GovernanceError(f"未知复核动作：{action}")

        case.status = "resolved"
        case.resolution = {
            "action": action,
            "by": reviewer,
            "on": day.isoformat(),
            "note": note,
            "replacement_publication_id": replacement_publication_id,
        }
        case.history.append({"on": day.isoformat(), "actor": reviewer, "action": action, "note": note})
        self.registry.add("review_cases", case, replace=True)
        self.audit("review_case_resolved", reviewer, day, case_id=case_id, resolution_action=action)
        return case

    # ------------------------------------------------------------------
    # 历史还原
    # ------------------------------------------------------------------
    def reconstruct_publication(self, publication_id: str) -> dict[str, Any]:
        pub = self.registry.get("publications", publication_id)
        if not pub.snapshot:
            raise GovernanceError(f"发布物 {publication_id} 没有不可变快照", code="snapshot_missing")
        return {
            "publication_id": publication_id,
            "current_record": pub.to_dict(),
            "snapshot": copy.deepcopy(pub.snapshot),
        }

    def reconstruct_campaign(self, campaign_id: str, *, as_of: str | date | None = None) -> dict[str, Any]:
        campaign = self.registry.get("campaigns", campaign_id)
        cutoff = parse_date(as_of) if as_of else None
        publications = []
        for pub in self.registry.data.publications:
            if pub.campaign_id != campaign_id:
                continue
            published = parse_date(pub.published_on)
            if cutoff and published and published > cutoff:
                continue
            item = {
                "publication": pub.to_dict(),
                "snapshot": copy.deepcopy(pub.snapshot),
            }
            publications.append(item)
        return {
            "campaign": campaign.to_dict(),
            "as_of": cutoff.isoformat() if cutoff else None,
            "publications": publications,
            "note": "snapshot 字段是发布时冻结的表述、审批和证据版本；后续实体变更不会改写它。",
        }


# 领域语言别名：论文/实验（study）以及产品批次/销售批次在本模型中由同一继承链承载。
GovernanceService.register_study = GovernanceService.register_experiment  # type: ignore[attr-defined]
GovernanceService.register_product_batch = GovernanceService.register_sales_batch  # type: ignore[attr-defined]
GovernanceService.confirm_product_batch_inheritance = (  # type: ignore[attr-defined]
    GovernanceService.confirm_sales_batch_inheritance
)

# 常见用例命名别名，便于不同调用习惯发现同一业务动作。
GovernanceService.supersede_experiment = GovernanceService.overturn_experiment  # type: ignore[attr-defined]
GovernanceService.approve_expression = GovernanceService.approve_claim  # type: ignore[attr-defined]
GovernanceService.record_publication = GovernanceService.publish_claim  # type: ignore[attr-defined]
