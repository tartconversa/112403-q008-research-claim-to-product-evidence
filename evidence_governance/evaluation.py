"""判定一条表述在指定日期、渠道和商品上下文里是否可作为已验证证据使用。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .store import Registry
from .utils import Issue, date_within, parse_date, strength_matches


class Report(dict):
    """同时支持字典键和属性访问的报告。"""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class Context:
    on_date: date
    channel: str | None = None
    product_id: str | None = None
    product_version_id: str | None = None
    formula_version_id: str | None = None
    sales_batch_id: str | None = None
    material_id: str | None = None
    material_batch_id: str | None = None
    condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "on_date": self.on_date.isoformat(),
            "channel": self.channel,
            "product_id": self.product_id,
            "product_version_id": self.product_version_id,
            "formula_version_id": self.formula_version_id,
            "sales_batch_id": self.sales_batch_id,
            "material_id": self.material_id,
            "material_batch_id": self.material_batch_id,
            "condition": self.condition,
        }


def current_product_version(registry: Registry, product_id: str, on_date: date | str) -> Any | None:
    target = parse_date(on_date, field_name="on_date") if isinstance(on_date, str) else on_date
    candidates = []
    for version in registry.data.product_versions:
        if version.product_id != product_id:
            continue
        start = parse_date(version.effective_from)
        end = parse_date(version.effective_to)
        if date_within(target, start, end):
            candidates.append(version)
    if not candidates:
        return None
    return max(candidates, key=lambda item: parse_date(item.effective_from) or date.min)


def resolve_context(
    registry: Registry,
    on_date: date | str,
    *,
    channel: str | None = None,
    product_id: str | None = None,
    product_version_id: str | None = None,
    formula_version_id: str | None = None,
    sales_batch_id: str | None = None,
    material_id: str | None = None,
    material_batch_id: str | None = None,
    condition: str | None = None,
) -> tuple[Context, list[Issue]]:
    issues: list[Issue] = []
    target_date = parse_date(on_date, field_name="on_date") if isinstance(on_date, str) else on_date
    ctx = Context(
        on_date=target_date,
        channel=channel,
        product_id=product_id,
        product_version_id=product_version_id,
        formula_version_id=formula_version_id,
        sales_batch_id=sales_batch_id,
        material_id=material_id,
        material_batch_id=material_batch_id,
        condition=condition,
    )

    if product_version_id:
        pv = registry.get("product_versions", product_version_id)
        if ctx.product_id and ctx.product_id != pv.product_id:
            issues.append(Issue("context_conflict", "商品与商品版本不一致", subject_id=product_version_id))
        ctx.product_id = pv.product_id
        if ctx.formula_version_id and ctx.formula_version_id != pv.formula_version_id:
            issues.append(Issue("context_conflict", "商品版本与配方版本不一致", subject_id=product_version_id))
        ctx.formula_version_id = pv.formula_version_id
        pv_start = parse_date(pv.effective_from)
        pv_end = parse_date(pv.effective_to)
        if not date_within(ctx.on_date, pv_start, pv_end):
            current = current_product_version(registry, pv.product_id, ctx.on_date)
            has_successor = bool(current and current.id != product_version_id)
            if not has_successor:
                # 若已有当前后继版本，扫描层会生成更直接的 formula_changed 案件，
                # 这里不再重复报窗口结束。
                issues.append(
                    Issue(
                        "product_version_window_ended",
                        f"商品版本 {product_version_id} 不在 {ctx.on_date.isoformat()} 的登记销售/生效窗口内；"
                        "需产品负责人确认是否仍有旧批次流通",
                        severity="warning",
                        subject_type="product_version",
                        subject_id=product_version_id,
                    )
                )

    if sales_batch_id:
        batch = registry.get("sales_batches", sales_batch_id)
        if ctx.product_id and ctx.product_id != batch.product_id:
            issues.append(Issue("context_conflict", "销售批次与商品不一致", subject_id=sales_batch_id))
        if ctx.product_version_id and ctx.product_version_id != batch.product_version_id:
            issues.append(Issue("context_conflict", "销售批次与商品版本不一致", subject_id=sales_batch_id))
        ctx.product_id = batch.product_id
        ctx.product_version_id = batch.product_version_id
        pv = registry.find("product_versions", batch.product_version_id)
        if pv:
            ctx.formula_version_id = pv.formula_version_id
        if batch.inheritance_state != "confirmed":
            issues.append(
                Issue(
                    "sales_batch_inheritance_unconfirmed",
                    f"销售批次 {sales_batch_id} 的继承关系尚未由产品负责人确认",
                    severity="gap",
                    subject_type="sales_batch",
                    subject_id=sales_batch_id,
                )
            )

    if material_batch_id:
        lot = registry.find("material_batches", material_batch_id)
        if lot and ctx.material_id is None:
            ctx.material_id = lot.material_id
        elif lot and ctx.material_id and lot.material_id != ctx.material_id:
            issues.append(Issue("context_conflict", "原料批次与原料不一致", subject_id=material_batch_id))

    return ctx, issues


def target_material_profile(registry: Registry, ctx: Context) -> dict[str, dict[str, Any]]:
    """汇总目标配方/版本中显式登记的原料、浓度和原料批次。"""
    profile: dict[str, dict[str, Any]] = {}

    def ensure(material_id: str) -> dict[str, Any]:
        return profile.setdefault(
            material_id,
            {"material_id": material_id, "concentrations": [], "material_batches": []},
        )

    if ctx.formula_version_id:
        formula = registry.find("formulas", ctx.formula_version_id)
        if formula:
            for component in formula.components:
                item = ensure(component.material_id)
                if component.concentration:
                    item["concentrations"].append(component.concentration)

    if ctx.product_version_id:
        for use in registry.data.material_lot_uses:
            if use.product_version_id != ctx.product_version_id:
                continue
            use_start = parse_date(use.valid_from)
            use_end = parse_date(use.valid_to)
            if not date_within(ctx.on_date, use_start, use_end):
                continue
            item = ensure(use.material_id)
            if use.material_batch_id:
                item["material_batches"].append(use.material_batch_id)

    if ctx.material_id:
        item = ensure(ctx.material_id)
        if ctx.material_batch_id:
            item["material_batches"].append(ctx.material_batch_id)

    return profile


def _approval_issues(registry: Registry, approval: Any, ctx: Context) -> list[Issue]:
    issues: list[Issue] = []
    if approval.state != "approved":
        issues.append(
            Issue(
                "approval_not_approved",
                f"审批 {approval.id} 状态为 {approval.state}，不是有效批准",
                subject_type="approval",
                subject_id=approval.id,
            )
        )

    start = parse_date(approval.valid_from)
    end = parse_date(approval.valid_to)
    if not date_within(ctx.on_date, start, end):
        if end and ctx.on_date > end:
            code = "approval_expired"
            message = f"审批 {approval.id} 已于 {approval.valid_to} 到期"
        elif start and ctx.on_date < start:
            code = "approval_not_yet_valid"
            message = f"审批 {approval.id} 自 {approval.valid_from} 起生效"
        else:
            code = "approval_date_invalid"
            message = f"审批 {approval.id} 不在有效期间"
        issues.append(Issue(code, message, subject_type="approval", subject_id=approval.id))
    if not approval.valid_from:
        issues.append(
            Issue(
                "approval_start_missing",
                f"审批 {approval.id} 未登记生效日期，无法完整还原审批期间",
                severity="gap",
                subject_type="approval",
                subject_id=approval.id,
            )
        )

    if ctx.channel:
        channels = set(approval.channels or [])
        if "*" not in channels and ctx.channel not in channels:
            issues.append(
                Issue(
                    "channel_not_approved",
                    f"渠道 {ctx.channel!r} 不在审批 {approval.id} 的渠道 {sorted(channels)!r} 中",
                    subject_type="approval",
                    subject_id=approval.id,
                    details={"allowed_channels": sorted(channels)},
                )
            )

    scope: Any = approval.scope
    if ctx.product_version_id:
        product_version_allowed = ctx.product_version_id in (scope.product_version_ids or [])
        product_family_allowed = (
            ctx.product_id in (scope.product_ids or []) and bool(scope.all_product_versions)
        )
        if not (product_version_allowed or product_family_allowed):
            issues.append(
                Issue(
                    "product_version_not_approved",
                    f"审批 {approval.id} 未明确覆盖商品版本 {ctx.product_version_id}",
                    subject_type="approval",
                    subject_id=approval.id,
                )
            )

    if ctx.product_id and not ctx.product_version_id:
        if ctx.product_id not in (scope.product_ids or []) and not any(
            registry.find("product_versions", pvid)
            and registry.find("product_versions", pvid).product_id == ctx.product_id
            for pvid in scope.product_version_ids
        ):
            issues.append(
                Issue(
                    "product_not_in_approval_scope",
                    f"审批 {approval.id} 未覆盖商品 {ctx.product_id}",
                    subject_type="approval",
                    subject_id=approval.id,
                )
            )

    if ctx.formula_version_id:
        exact_formula = ctx.formula_version_id in (scope.formula_version_ids or [])
        exact_version = ctx.product_version_id in (scope.product_version_ids or [])
        if not (exact_formula or exact_version):
            issues.append(
                Issue(
                    "formula_version_not_approved",
                    f"审批 {approval.id} 未明确覆盖配方版本 {ctx.formula_version_id}；配方升级不自动继承旧审批",
                    subject_type="approval",
                    subject_id=approval.id,
                )
            )

    if ctx.sales_batch_id:
        if ctx.sales_batch_id not in (scope.sales_batch_ids or []):
            batch = registry.find("sales_batches", ctx.sales_batch_id)
            inherited_version_allowed = bool(
                batch
                and ctx.product_version_id in (scope.product_version_ids or [])
                and batch.inheritance_state == "confirmed"
            )
            if not inherited_version_allowed:
                issues.append(
                    Issue(
                        "sales_batch_not_approved",
                        f"销售批次 {ctx.sales_batch_id} 不在审批 {approval.id} 的明确范围内，"
                        "或其继承关系未经产品负责人确认",
                        subject_type="approval",
                        subject_id=approval.id,
                    )
                )

    if not (scope.product_ids or scope.product_version_ids or scope.formula_version_ids):
        issues.append(
            Issue(
                "approval_scope_empty",
                f"审批 {approval.id} 没有登记任何商品/配方版本范围",
                subject_type="approval",
                subject_id=approval.id,
            )
        )
    return issues


def evaluate_evidence_ref(
    registry: Registry,
    ref: Any,
    ctx: Context,
    profile: dict[str, dict[str, Any]],
) -> tuple[list[Issue], dict[str, Any]]:
    issues: list[Issue] = []
    experiment = registry.find("experiments", ref.experiment_id)
    summary: dict[str, Any] = {
        "experiment_id": ref.experiment_id,
        "experiment_version": ref.experiment_version,
        "stance": ref.stance,
        "applicability_id": ref.applicability_id,
        "found": experiment is not None,
    }
    if experiment is None:
        issues.append(
            Issue(
                "experiment_not_found",
                f"证据 {ref.experiment_id} 未登记",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )
        summary["issues"] = [i.to_dict() for i in issues]
        return issues, summary

    version = experiment.get_version(ref.experiment_version)
    summary["title"] = experiment.title
    summary["version"] = version.to_dict() if version else None

    if not getattr(ref, "version_explicit", True):
        issues.append(
            Issue(
                "evidence_version_not_explicit",
                f"表述引用 {ref.experiment_id} 时未写明版本；当前锁定 v{ref.experiment_version} 需复核",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )

    if version is None:
        issues.append(
            Issue(
                "evidence_version_missing",
                f"实验 {ref.experiment_id} 不存在已登记版本 v{ref.experiment_version}",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )
        summary["issues"] = [i.to_dict() for i in issues]
        return issues, summary

    if version.status in {"superseded", "retracted"} or version.overturned_by_experiment_id:
        issues.append(
            Issue(
                "evidence_overturned",
                f"证据 {ref.experiment_id} v{version.version} 已被推翻、替代或撤回",
                subject_type="experiment",
                subject_id=ref.experiment_id,
                details={
                    "status": version.status,
                    "overturned_by_experiment_id": version.overturned_by_experiment_id,
                    "overturned_by_version": version.overturned_by_version,
                },
            )
        )

    if ref.stance == "refutes":
        issues.append(
            Issue(
                "evidence_refutes_claim",
                f"{ref.experiment_id} v{ref.experiment_version} 的显式立场是反驳该表述，不能作为支持证据",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )

    applicability = None
    if ref.applicability_id:
        applicability = registry.find("applicabilities", ref.applicability_id)
        if applicability is None:
            issues.append(
                Issue(
                    "applicability_not_found",
                    f"适用条件确认 {ref.applicability_id} 未登记",
                    subject_type="applicability",
                    subject_id=ref.applicability_id,
                )
            )
    else:
        all_matches = [
            item
            for item in registry.data.applicabilities
            if item.experiment_id == ref.experiment_id
            and item.experiment_version == ref.experiment_version
        ]
        confirmed_matches = [item for item in all_matches if item.state == "confirmed"]
        if len(all_matches) == 1:
            applicability = all_matches[0]
        elif len(confirmed_matches) == 1:
            applicability = confirmed_matches[0]
        elif len(all_matches) > 1:
            issues.append(
                Issue(
                    "applicability_ambiguous",
                    f"实验 {ref.experiment_id} v{ref.experiment_version} 有多条适用范围，"
                    "审批必须显式引用 applicability_id",
                    severity="gap",
                    subject_type="experiment",
                    subject_id=ref.experiment_id,
                )
            )

    evidence_material_id = version.material_id
    evidence_concentration = version.concentration
    evidence_condition = version.condition

    if applicability:
        summary["applicability"] = applicability.to_dict()
        evidence_material_id = applicability.material_id or evidence_material_id
        evidence_concentration = applicability.concentration or evidence_concentration
        evidence_condition = applicability.condition or evidence_condition
        if applicability.state != "confirmed":
            if applicability.missing:
                for missing_code in applicability.missing:
                    issues.append(
                        Issue(
                            f"missing_{missing_code}",
                            f"适用范围 {applicability.id} 尚未确认，缺少 {missing_code}",
                            severity="gap",
                            subject_type="applicability",
                            subject_id=applicability.id,
                        )
                    )
            else:
                issues.append(
                    Issue(
                        "applicability_unconfirmed",
                        f"适用范围 {applicability.id} 尚未由研发完整确认",
                        severity="gap",
                        subject_type="applicability",
                        subject_id=applicability.id,
                    )
                )
    else:
        issues.append(
            Issue(
                "applicability_unconfirmed",
                f"实验 {ref.experiment_id} v{ref.experiment_version} 没有经研发确认的原料批次、浓度和条件适用范围",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )

    summary["material_id"] = evidence_material_id
    summary["concentration"] = evidence_concentration
    summary["condition"] = evidence_condition

    # 以下只使用显式 ID 与结构化字段，绝不使用原料名称/关键词相似性。
    if not evidence_material_id:
        issues.append(
            Issue(
                "evidence_material_missing",
                f"实验 {ref.experiment_id} v{ref.experiment_version} 未登记受测原料",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )
    elif profile and evidence_material_id not in profile:
        issues.append(
            Issue(
                "material_scope_mismatch",
                f"实验原料 {evidence_material_id} 不在目标商品/配方显式登记的原料范围内；"
                "不能因名称或关键词相同而认定支持",
                subject_type="experiment",
                subject_id=ref.experiment_id,
                details={"evidence_material_id": evidence_material_id},
            )
        )
    elif not profile:
        issues.append(
            Issue(
                "product_material_context_missing",
                "缺少目标配方/商品版本的显式原料关联，无法核对实验原料范围",
                severity="gap",
                subject_type="product_version",
                subject_id=ctx.product_version_id or ctx.formula_version_id,
            )
        )

    target_material = profile.get(evidence_material_id or "", {})

    if applicability and applicability.scope == "batch_specific":
        required_batch = applicability.material_batch_id
        target_batches = set(target_material.get("material_batches", []))
        if not required_batch:
            issues.append(
                Issue(
                    "batch_scope_missing",
                    f"适用范围 {applicability.id} 标为批次限定但未给出原料批次",
                    severity="gap",
                    subject_type="applicability",
                    subject_id=applicability.id,
                )
            )
        elif target_batches and required_batch not in target_batches:
            issues.append(
                Issue(
                    "material_batch_mismatch",
                    f"实验仅适用于原料批次 {required_batch}，目标版本使用 {sorted(target_batches)}",
                    subject_type="applicability",
                    subject_id=applicability.id,
                )
            )
        elif not target_batches:
            issues.append(
                Issue(
                    "target_batch_missing",
                    f"无法确认目标商品是否使用实验限定批次 {required_batch}",
                    severity="gap",
                    subject_type="product_version",
                    subject_id=ctx.product_version_id,
                )
            )
    elif applicability and applicability.scope == "unspecified" and "batch_scope" not in applicability.missing:
        issues.append(
            Issue(
                "batch_applicability_unspecified",
                f"实验 {ref.experiment_id} v{ref.experiment_version} 未说明是否仅限特定原料批次",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )

    target_concentrations = target_material.get("concentrations", [])
    if evidence_concentration and target_concentrations:
        matches = [strength_matches(evidence_concentration, item) for item in target_concentrations]
        if any(matches):
            pass
        elif all(result is False for result in matches):
            issues.append(
                Issue(
                    "concentration_mismatch",
                    f"实验浓度 {evidence_concentration} 与配方浓度 {target_concentrations} 不一致",
                    subject_type="experiment",
                    subject_id=ref.experiment_id,
                )
            )
        else:
            issues.append(
                Issue(
                    "concentration_not_comparable",
                    f"实验浓度 {evidence_concentration} 与配方浓度 {target_concentrations} 无法可靠比较，需人工确认",
                    severity="gap",
                    subject_type="experiment",
                    subject_id=ref.experiment_id,
                )
            )
    elif not evidence_concentration:
        issues.append(
            Issue(
                "study_concentration_missing",
                f"实验 {ref.experiment_id} v{ref.experiment_version} 未登记浓度条件",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )
    elif not target_concentrations:
        issues.append(
            Issue(
                "formula_concentration_missing",
                "配方/商品未登记该原料浓度，无法确认实验浓度是否适用于新版本",
                severity="gap",
                subject_type="formula_version",
                subject_id=ctx.formula_version_id,
            )
        )

    if not evidence_condition:
        issues.append(
            Issue(
                "study_condition_missing",
                f"实验 {ref.experiment_id} v{ref.experiment_version} 未登记测试条件",
                severity="gap",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )
    if ctx.condition and evidence_condition and ctx.condition != evidence_condition:
        issues.append(
            Issue(
                "condition_mismatch",
                f"使用条件 {ctx.condition!r} 与实验条件 {evidence_condition!r} 不一致",
                subject_type="experiment",
                subject_id=ref.experiment_id,
            )
        )

    summary["issues"] = [issue.to_dict() for issue in issues]
    return issues, summary


def evaluate_claim_use(
    registry: Registry,
    claim_id: str,
    on_date: date | str | None = None,
    *,
    approval_id: str | None = None,
    channel: str | None = None,
    product_id: str | None = None,
    product_version_id: str | None = None,
    formula_version_id: str | None = None,
    sales_batch_id: str | None = None,
    material_id: str | None = None,
    material_batch_id: str | None = None,
    condition: str | None = None,
) -> Report:
    target_date = parse_date(on_date) if on_date is not None else date.today()
    claim = registry.get("claims", claim_id)
    ctx, context_issues = resolve_context(
        registry,
        target_date,
        channel=channel,
        product_id=product_id,
        product_version_id=product_version_id,
        formula_version_id=formula_version_id,
        sales_batch_id=sales_batch_id,
        material_id=material_id,
        material_batch_id=material_batch_id,
        condition=condition,
    )
    issues = list(context_issues)

    approval = None
    if approval_id:
        approval = registry.get("approvals", approval_id)
        if approval.claim_id != claim_id:
            issues.append(
                Issue(
                    "approval_claim_mismatch",
                    f"审批 {approval_id} 不属于表述 {claim_id}",
                    subject_type="approval",
                    subject_id=approval_id,
                )
            )
    else:
        candidates = list(registry.all_approvals_for_claim(claim_id))
        # 优先选择当前上下文中问题最少的 explicit approved 审批。
        scored = []
        for candidate in candidates:
            candidate_issues = _approval_issues(registry, candidate, ctx)
            blocker_count = sum(i.severity == "blocker" for i in candidate_issues)
            scored.append((blocker_count, len(candidate_issues), candidate.decided_on or "", candidate))
        if scored:
            scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=False)
            approval = scored[0][3]

    if approval:
        issues.extend(_approval_issues(registry, approval, ctx))
        if approval.claim_revision != claim.current_revision:
            current_text = claim.get_text_version(approval.claim_revision)
            if current_text is None:
                issues.append(
                    Issue(
                        "approved_revision_missing",
                        f"审批 {approval.id} 针对 v{approval.claim_revision}，但该文本版本不存在",
                        subject_type="claim",
                        subject_id=claim_id,
                    )
                )
            elif claim.current_revision != approval.claim_revision:
                issues.append(
                    Issue(
                        "claim_revision_changed",
                        f"表述当前为 v{claim.current_revision}，审批 {approval.id} 仅批准 v{approval.claim_revision}",
                        subject_type="claim",
                        subject_id=claim_id,
                    )
                )
        refs = approval.evidence_refs
        approval_dict = approval.to_dict()
    else:
        issues.append(
            Issue(
                "no_approval",
                f"表述 {claim_id} 没有可用于该上下文的合规批准",
                subject_type="claim",
                subject_id=claim_id,
            )
        )
        refs = claim.evidence_links
        approval_dict = None

    profile = target_material_profile(registry, ctx)
    evidence_summaries: list[dict[str, Any]] = []
    if not refs:
        issues.append(
            Issue(
                "no_evidence_refs",
                f"表述 {claim_id} 没有在表述或审批中冻结任何证据引用，不能作为已验证传播表述使用",
                subject_type="claim",
                subject_id=claim_id,
            )
        )
    for ref in refs:
        ref_issues, summary = evaluate_evidence_ref(registry, ref, ctx, profile)
        issues.extend(ref_issues)
        evidence_summaries.append(summary)

    blockers = [i for i in issues if i.severity == "blocker"]
    gaps = [i for i in issues if i.severity == "gap"]
    warnings = [i for i in issues if i.severity == "warning"]
    approved_for_scope = approval is not None and not any(
        i.code
        in {
            "approval_not_approved",
            "approval_expired",
            "approval_not_yet_valid",
            "approval_date_invalid",
            "channel_not_approved",
            "product_version_not_approved",
            "product_not_in_approval_scope",
            "formula_version_not_approved",
            "sales_batch_not_approved",
            "approval_scope_empty",
            "context_conflict",
            "claim_revision_changed",
            "approved_revision_missing",
            "approval_claim_mismatch",
        }
        for i in blockers
    )
    evidence_verified = not blockers and not gaps and bool(refs)
    legacy_scope_usable = (
        not blockers and not gaps and approval is not None and approval.state == "approved"
    )
    window_codes = {issue.code for issue in warnings} & {"product_version_window_ended"}
    in_effective_window = not window_codes
    # usable 表示可在该日期作为当前商品表述使用；旧版本窗口结束但审批/证据本身仍成立时，
    # legacy_scope_usable=True，可由产品负责人按“旧批次保留”显式复核。
    usable = legacy_scope_usable and in_effective_window
    blocker_codes = {issue.code for issue in blockers}
    if usable:
        status = "usable"
    elif legacy_scope_usable and window_codes:
        status = "legacy_version_review_required"
    elif "approval_expired" in blocker_codes:
        status = "expired"
    elif "no_approval" in blocker_codes or "approval_not_approved" in blocker_codes:
        status = "not_approved"
    elif blocker_codes & {
        "evidence_overturned",
        "evidence_refutes_claim",
        "material_scope_mismatch",
        "material_batch_mismatch",
        "concentration_mismatch",
        "condition_mismatch",
        "evidence_version_missing",
    }:
        status = "evidence_conflict"
    elif gaps:
        status = "missing_evidence"
    else:
        status = "blocked"

    return Report(
        status=status,
        claim_id=claim_id,
        claim_revision=claim.current_revision,
        text=claim.text,
        on_date=target_date.isoformat(),
        context=ctx.to_dict(),
        approval=approval_dict,
        approved_for_scope=approved_for_scope,
        evidence_verified=evidence_verified,
        in_effective_window=in_effective_window,
        legacy_scope_usable=legacy_scope_usable,
        usable=usable,
        blockers=[i.to_dict() for i in blockers],
        missing_evidence=[i.to_dict() for i in gaps],
        warnings=[i.to_dict() for i in warnings],
        # 常见命名别名，方便不同调用方按 gaps/evidence_gaps/eligible 读取。
        blocking_issues=[i.to_dict() for i in blockers],
        gaps=[i.to_dict() for i in gaps],
        evidence_gaps=[i.to_dict() for i in gaps],
        eligible=usable,
        issues=[i.to_dict() for i in issues],
        evidence=evidence_summaries,
        target_material_profile=profile,
    )
