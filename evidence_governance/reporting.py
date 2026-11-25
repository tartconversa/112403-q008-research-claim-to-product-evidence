"""把治理结果渲染成适合终端阅读的中文报告。"""

from __future__ import annotations

import json
from typing import Any


def print_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _issue_line(issue: dict[str, Any], indent: str = "    ") -> str:
    icon = {"blocker": "✗", "gap": "?", "warning": "!"}.get(issue.get("severity"), "-")
    return f"{indent}{icon} [{issue['code']}] {issue['message']}"


def render_claim_report(report: dict[str, Any]) -> str:
    lines = [
        f"表述 {report['claim_id']} v{report['claim_revision']}：{report['text']}",
        f"状态：{report.get('status', 'unknown')}｜日期 {report['on_date']}｜渠道 {report['context'].get('channel') or '未指定'}｜"
        f"可作为已验证表述使用：{'是' if report['usable'] else '否'}",
        f"审批范围匹配：{'是' if report['approved_for_scope'] else '否'}｜"
        f"证据完整：{'是' if report['evidence_verified'] else '否'}",
    ]
    if report.get("approval"):
        approval = report["approval"]
        lines.append(
            "审批："
            f"{approval['id']} / {approval['state']} / 渠道={','.join(approval.get('channels') or []) or '未登记'} / "
            f"期间={approval.get('valid_from') or '?'}~{approval.get('valid_to') or '开放'}"
        )
    else:
        lines.append("审批：无")
    for title, key in (("阻断问题", "blockers"), ("缺少的证据", "missing_evidence"), ("警告", "warnings")):
        items = report.get(key) or []
        if items:
            lines.append(f"{title}（{len(items)}）：")
            lines.extend(_issue_line(item) for item in items)
    if report.get("evidence"):
        lines.append("引用证据：")
        for evidence in report["evidence"]:
            lines.append(
                f"  - {evidence['experiment_id']} v{evidence['experiment_version']} "
                f"({evidence.get('stance')})；原料={evidence.get('material_id') or '?'}，"
                f"浓度={evidence.get('concentration') or '?'}，条件={evidence.get('condition') or '?'}"
            )
    return "\n".join(lines)


def render_guide(guide: dict[str, Any]) -> str:
    lines = [f"表述 {guide['claim_id']} v{guide['current_revision']}：{guide['text']}", "审批与适用范围："]
    if not guide["approvals"]:
        lines.append("  （无审批记录）")
    for approval in guide["approvals"]:
        lines.append(
            f"  - {approval['approval_id']} [{approval['state']}] "
            f"当前有效={'是' if approval['currently_effective'] else '否'}；"
            f"渠道：{', '.join(approval['channels']) or '未登记'}；"
            f"期间：{approval['valid_from'] or '?'} ~ {approval['valid_to'] or '开放'}"
        )
        pvs = ", ".join(item["id"] for item in approval["product_versions"]) or "-"
        formulas = ", ".join(item["id"] for item in approval["formula_versions"]) or "-"
        lines.append(f"    商品版本：{pvs}；配方版本：{formulas}")
        if approval["evidence_refs"]:
            refs = ", ".join(f"{r['experiment_id']}#v{r['experiment_version']}" for r in approval["evidence_refs"])
            lines.append(f"    冻结证据版本：{refs}")
    return "\n".join(lines)


def render_dashboard(dashboard: dict[str, Any]) -> str:
    lines = [
        f"复核看板（{dashboard['on_date']}）",
        f"待处理复核案件：{dashboard['open_case_count']}",
        "",
        "页面/物料状态：",
    ]
    for pub in dashboard["publications"]:
        location = f" ({pub['location']})" if pub.get("location") else ""
        lines.append(
            f"  [{pub['display_state']:^12}] {pub['publication_id']} / {pub['asset_id']}"
            f" {pub.get('asset_name') or ''}{location}"
        )
        lines.append(
            f"      表述={pub['claim_id']} 渠道={pub['channel']} "
            f"活动={pub.get('campaign_id') or '-'} 发布={pub['published_on']}"
        )
        if pub["open_case_ids"]:
            lines.append("      案件=" + ", ".join(pub["open_case_ids"]))
    if dashboard["open_cases"]:
        lines.extend(["", "待复核问题："])
        for case in dashboard["open_cases"]:
            lines.append(f"  {case['id']} [{case['severity']}/{case['code']}] {case['publication_id']}")
            lines.append(f"      {case['message']}")
    return "\n".join(lines)


def render_reconstruction(payload: dict[str, Any]) -> str:
    if "campaign" in payload:
        campaign = payload["campaign"]
        lines = [
            f"活动还原：{campaign['id']} {campaign.get('name') or ''}",
            f"周期：{campaign.get('started_on') or '?'} ~ {campaign.get('ended_on') or '进行中'}",
            f"包含发布快照：{len(payload['publications'])}",
        ]
        for item in payload["publications"]:
            snapshot = item["snapshot"]
            claim = snapshot.get("claim", {})
            approval = snapshot.get("approval") or {}
            lines.append(
                f"  - {item['publication']['id']}: {claim.get('text')} "
                f"[{approval.get('id', '无审批')} / {','.join(approval.get('channels') or [])}]"
            )
            refs = approval.get("evidence_refs") or []
            if refs:
                lines.append("      证据版本：" + ", ".join(f"{r['experiment_id']}#v{r['experiment_version']}" for r in refs))
        lines.append(payload.get("note", ""))
        return "\n".join(lines)

    snapshot = payload["snapshot"]
    claim = snapshot.get("claim", {})
    approval = snapshot.get("approval") or {}
    lines = [
        f"发布物还原：{payload['publication_id']}",
        f"发布时表述 v{claim.get('revision')}：{claim.get('text')}",
        f"发布时审批：{approval.get('id', '无')} / {approval.get('state', '未批准')} / "
        f"{approval.get('valid_from') or '?'}~{approval.get('valid_to') or '开放'}",
        "发布时冻结证据：",
    ]
    for evidence in snapshot.get("evidence_at_publication", []):
        lines.append(
            f"  - {evidence.get('experiment_id')}#v{evidence.get('experiment_version')} "
            f"原料={evidence.get('material_id')} 浓度={evidence.get('concentration')} 条件={evidence.get('condition')}"
        )
    decision = snapshot.get("decision_at_publication", {})
    lines.append(f"导入/发布时判定 usable={decision.get('usable')} evidence_verified={decision.get('evidence_verified')}")
    return "\n".join(lines)
