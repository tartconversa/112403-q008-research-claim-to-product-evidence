"""命令行入口。

示例：

.. code-block:: bash

    python -m evidence_governance sample-report --date 2026-11-25
    python -m evidence_governance seed-demo registry.json
    python -m evidence_governance dashboard --registry registry.json
    python -m evidence_governance reconstruct --registry registry.json --campaign CAMP-SUMMER
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from .csv_import import registry_from_samples
from .demo import build_demo_registry
from .reporting import print_json, render_claim_report, render_dashboard, render_guide, render_reconstruction
from .service import GovernanceService
from .store import Registry
from .utils import GovernanceError

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES = ROOT / "samples"


def _load_registry_or_samples(path: str | None) -> Registry:
    return Registry.load(path) if path else registry_from_samples(DEFAULT_SAMPLES)


def _print(payload: Any, as_json: bool, renderer=None) -> None:
    if as_json:
        print(print_json(payload))
    elif renderer:
        print(renderer(payload))
    else:
        print(payload)


def cmd_sample_report(args: argparse.Namespace) -> int:
    registry = registry_from_samples(args.samples)
    service = GovernanceService(registry)
    dashboard = service.review_dashboard(args.date)
    if args.json:
        print(print_json(dashboard))
    else:
        print(render_dashboard(dashboard))
        print("\n提示：样例 CSV 未提供表述正文、配方浓度、R&D 确认人和审批中的证据版本，")
        print("因此 U2/C2 虽有 approved 源状态，仍会被标为证据不完整，而不是静默视为完全有效。")
    return 0


def cmd_import_samples(args: argparse.Namespace) -> int:
    registry = registry_from_samples(args.samples)
    target = registry.save(args.out)
    print(f"已导入样例并保存到 {target}")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    registry = build_demo_registry()
    target = registry.save(args.out)
    print(f"已写入完整演示登记处：{target}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    dashboard = service.review_dashboard(args.date)
    if args.registry:
        registry.save(args.registry)  # 持久化本轮幂等扫描出的案件
    _print(dashboard, args.json, render_dashboard)
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    report = evaluate(service, args)
    _print(report, args.json, render_claim_report)
    return 0


def evaluate(service: GovernanceService, args: argparse.Namespace) -> dict[str, Any]:
    from .evaluation import evaluate_claim_use

    return evaluate_claim_use(
        service.registry,
        args.claim_id,
        args.date,
        channel=args.channel,
        product_id=args.product_id,
        product_version_id=args.product_version,
        formula_version_id=args.formula_version,
        sales_batch_id=args.sales_batch,
    )


def cmd_guide(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    guide = service.claim_usage_guide(args.claim_id, args.date)
    _print(guide, args.json, render_guide)
    return 0


def cmd_using(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    items = service.publications_using_claim(
        args.claim_id, args.date, active_only=not args.include_ended
    )
    if args.json:
        print(print_json(items))
    else:
        for item in items:
            pub = item["publication"]
            display = item.get("display") or {}
            asset = item.get("asset") or {}
            print(
                f"[{display.get('display_state', 'unknown')}] {pub['id']} "
                f"{asset.get('kind', '')} {asset.get('location') or asset.get('name') or ''} "
                f"渠道={pub['channel']} 活动={pub.get('campaign_id') or '-'}"
            )
    return 0


def cmd_publications(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    dashboard = service.review_dashboard(args.date)
    if args.registry:
        registry.save(args.registry)
    _print(dashboard["publications"], args.json, lambda rows: "\n".join(
        f"[{row['display_state']}] {row['publication_id']} {row['claim_id']} ({row['channel']})"
        for row in rows
    ))
    return 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    registry = _load_registry_or_samples(args.registry)
    service = GovernanceService(registry)
    if args.campaign:
        payload = service.reconstruct_campaign(args.campaign, as_of=args.as_of)
    elif args.publication:
        payload = service.reconstruct_publication(args.publication)
    else:
        raise GovernanceError("必须提供 --publication 或 --campaign")
    _print(payload, args.json, render_reconstruction)
    return 0


def cmd_resolve_case(args: argparse.Namespace) -> int:
    if not args.registry:
        raise GovernanceError("resolve-case 会修改登记处，必须通过 --registry 指定 JSON 文件")
    registry = Registry.load(args.registry)
    service = GovernanceService(registry)
    case = service.resolve_review_case(
        args.case_id,
        args.by,
        args.action,
        args.note,
        args.date,
        replacement_publication_id=args.replacement,
    )
    # 立即做一次幂等扫描：下线/替换后自动关闭同发布物的兄弟案件，并把结果写入文件。
    service.scan_for_review(args.date)
    registry.save(args.registry)
    _print(case.to_dict(), args.json, lambda data: f"案件 {data['id']} 已处理：{data['status']} / {data['resolution']}")
    return 0


def cmd_confirm_applicability(args: argparse.Namespace) -> int:
    if not args.registry:
        raise GovernanceError("此命令会修改登记处，必须通过 --registry 指定 JSON 文件")
    registry = Registry.load(args.registry)
    service = GovernanceService(registry)
    item = service.confirm_applicability(args.applicability_id, args.by, args.date)
    registry.save(args.registry)
    _print(item.to_dict(), args.json, lambda data: f"适用范围 {data['id']} 已由 {data['confirmed_by']} 确认")
    return 0


def cmd_confirm_lineage(args: argparse.Namespace) -> int:
    if not args.registry:
        raise GovernanceError("此命令会修改登记处，必须通过 --registry 指定 JSON 文件")
    registry = Registry.load(args.registry)
    service = GovernanceService(registry)
    item = service.confirm_formula_lineage(args.formula_id, args.parent, args.by, args.date)
    registry.save(args.registry)
    _print(item.to_dict(), args.json, lambda data: f"配方继承 {data['parent_id']} -> {data['id']} 已确认")
    return 0


def cmd_confirm_batch(args: argparse.Namespace) -> int:
    if not args.registry:
        raise GovernanceError("此命令会修改登记处，必须通过 --registry 指定 JSON 文件")
    registry = Registry.load(args.registry)
    service = GovernanceService(registry)
    item = service.confirm_sales_batch_inheritance(
        args.batch_id, args.by, args.date, parent_sales_batch_id=args.parent
    )
    registry.save(args.registry)
    _print(item.to_dict(), args.json, lambda data: f"销售/产品批次 {data['id']} 的继承关系已确认")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidence-governance", description="研发证据—审批—传播版本复核系统")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--registry", help="JSON 登记处路径；claim/guide 缺省时读取内置 samples")
    common.add_argument("--date", help="复核日期 YYYY-MM-DD，默认今天")
    common.add_argument("--json", action="store_true", help="输出 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample-report", parents=[common], help="直接读取 samples/ 并输出复核看板")
    sample.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    sample.set_defaults(func=cmd_sample_report)

    imp = sub.add_parser("import-samples", parents=[common], help="把 CSV 样例转成 JSON 登记处")
    imp.add_argument("out")
    imp.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    imp.set_defaults(func=cmd_import_samples)

    demo = sub.add_parser("seed-demo", parents=[common], help="写入完整端到端合成演示数据")
    demo.add_argument("out")
    demo.set_defaults(func=cmd_seed_demo)

    dash = sub.add_parser("dashboard", parents=[common], help="扫描影响并输出复核看板")
    dash.set_defaults(func=cmd_dashboard)

    pubs = sub.add_parser("publications", parents=[common], help="列出页面/物料当前显示状态")
    pubs.set_defaults(func=cmd_publications)

    claim = sub.add_parser("claim", parents=[common], help="检查表述在指定上下文是否可作为已验证表述使用")
    claim.add_argument("claim_id")
    claim.add_argument("--channel")
    claim.add_argument("--product-id", dest="product_id")
    claim.add_argument("--product-version", dest="product_version")
    claim.add_argument("--formula-version", dest="formula_version")
    claim.add_argument("--sales-batch", dest="sales_batch")
    claim.set_defaults(func=cmd_claim)

    guide = sub.add_parser("guide", parents=[common], help="列出已批准表述适用的商品版本、渠道和期间")
    guide.add_argument("claim_id")
    guide.set_defaults(func=cmd_guide)

    using = sub.add_parser("using", parents=[common], help="列出引用某表述的页面/物料")
    using.add_argument("claim_id")
    using.add_argument("--include-ended", action="store_true")
    using.set_defaults(func=cmd_using)

    recon = sub.add_parser("reconstruct", parents=[common], help="还原发布时表述和证据版本")
    recon.add_argument("--publication")
    recon.add_argument("--campaign")
    recon.add_argument("--as-of", dest="as_of")
    recon.set_defaults(func=cmd_reconstruct)

    resolve = sub.add_parser("resolve-case", parents=[common], help="产品负责人复核/处置案件")
    resolve.add_argument("case_id")
    resolve.add_argument("--by", required=True)
    resolve.add_argument("--action", required=True, choices=[
        "removed", "take_down", "ended", "replaced", "legacy_confirmed",
        "evidence_supplemented", "scope_corrected", "reviewed_no_change",
    ])
    resolve.add_argument("--note", required=True)
    resolve.add_argument("--replacement", help="action=replaced 时的新发布物 ID")
    resolve.set_defaults(func=cmd_resolve_case)

    confirm_app = sub.add_parser("confirm-applicability", parents=[common], help="R&D 确认实验适用范围")
    confirm_app.add_argument("applicability_id")
    confirm_app.add_argument("--by", required=True)
    confirm_app.set_defaults(func=cmd_confirm_applicability)

    lineage = sub.add_parser("confirm-lineage", parents=[common], help="产品负责人确认配方继承")
    lineage.add_argument("formula_id")
    lineage.add_argument("--parent", required=True)
    lineage.add_argument("--by", required=True)
    lineage.set_defaults(func=cmd_confirm_lineage)

    batch = sub.add_parser("confirm-batch", parents=[common], help="产品负责人确认销售/产品批次继承")
    batch.add_argument("batch_id")
    batch.add_argument("--parent", help="父批次 ID；若批次已登记 parent 可省略")
    batch.add_argument("--by", required=True)
    batch.set_defaults(func=cmd_confirm_batch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # 顶层参数在子命令后也可出现；argparse 默认子解析器不继承，因此支持常见位置。
    if not hasattr(args, "registry"):
        args.registry = None
    try:
        return args.func(args)
    except GovernanceError as exc:
        print(f"错误 [{exc.code}]：{exc.message}", file=sys.stderr)
        if exc.details:
            print(print_json(exc.details), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"错误：找不到文件 {exc.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
