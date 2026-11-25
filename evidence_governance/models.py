"""领域模型。

这些数据类只保存显式登记的事实。系统不会根据名称或关键词相似性推断
原料、实验、配方和商品之间的证据关系。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Union, get_args, get_origin, get_type_hints


class Serializable:
    """支持嵌套 dataclass 与 JSON 原语互转。"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, data: Any) -> Any:
        if data is None:
            return None
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return data
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        valid_names = {item.name for item in fields(cls)}  # type: ignore[arg-type]
        for key, value in data.items():
            if key not in valid_names:
                # 读取未来版本或附加字段时忽略未知键，保证旧版本可打开新文件。
                continue
            kwargs[key] = _convert_value(value, hints.get(key, Any))
        return cls(**kwargs)


def _unwrap_optional(typ: Any) -> Any:
    origin = get_origin(typ)
    if origin is Union or origin is getattr(__import__("types"), "UnionType", object()):
        args = [arg for arg in get_args(typ) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return typ


def _convert_value(value: Any, typ: Any) -> Any:
    if value is None:
        return None
    typ = _unwrap_optional(typ)
    origin = get_origin(typ)
    if origin is list:
        item_type = get_args(typ)[0]
        return [_convert_value(item, item_type) for item in value]
    if origin is dict:
        return dict(value)
    if is_dataclass(typ) and isinstance(value, dict) and hasattr(typ, "from_dict"):
        return typ.from_dict(value)
    return value


@dataclass
class Paper(Serializable):
    id: str
    title: str = ""
    published_on: str | None = None
    source: str | None = None
    doi: str | None = None
    url: str | None = None


@dataclass
class Material(Serializable):
    id: str
    name: str = ""
    # keywords 仅用于检索展示；证据判定逻辑明确禁止使用它做相似性推断。
    keywords: list[str] = field(default_factory=list)


@dataclass
class MaterialBatch(Serializable):
    id: str
    material_id: str
    batch_code: str | None = None
    received_on: str | None = None
    note: str | None = None


@dataclass
class Protocol(Serializable):
    """实验方案；可被一个或多个实验版本引用。"""

    id: str
    title: str = ""
    protocol_version: str = "1"
    experiment_id: str | None = None
    objective: str | None = None
    method: str | None = None
    concentration: str | None = None
    condition: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None
    created_on: str | None = None


@dataclass
class ExperimentVersion(Serializable):
    version: int
    recorded_on: str | None = None
    protocol_id: str | None = None
    material_id: str | None = None
    concentration: str | None = None
    condition: str | None = None
    protocol: str | None = None
    result: str | None = None
    result_summary: str | None = None
    conclusion_id: str | None = None
    # active | superseded | retracted
    status: str = "active"
    overturned_by_experiment_id: str | None = None
    overturned_by_version: int | None = None
    overturn_reason: str | None = None


@dataclass
class Experiment(Serializable):
    id: str
    title: str = ""
    paper_id: str | None = None
    versions: list[ExperimentVersion] = field(default_factory=list)

    def get_version(self, version: int | None = None) -> ExperimentVersion | None:
        if not self.versions:
            return None
        if version is None:
            return max(self.versions, key=lambda item: item.version)
        for item in self.versions:
            if item.version == version:
                return item
        return None


@dataclass
class Applicability(Serializable):
    """研发确认的实验适用范围。

    scope:
    - ``material_wide``：研发明确确认可泛化到该原料而不限批次；
    - ``batch_specific``：仅适用于 ``material_batch_id`` 指定批次；
    - ``unspecified``：来源没有说明，必须标记证据缺口。
    """

    id: str
    experiment_id: str
    experiment_version: int
    material_id: str
    state: str = "incomplete"  # confirmed | incomplete
    scope: str = "unspecified"
    material_batch_id: str | None = None
    concentration: str | None = None
    condition: str | None = None
    confirmed_by: str | None = None
    confirmed_on: str | None = None
    missing: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class FormulaComponent(Serializable):
    material_id: str
    concentration: str | None = None
    role: str | None = None


@dataclass
class FormulaVersion(Serializable):
    id: str
    formula_code: str | None = None
    version_label: str | None = None
    parent_id: str | None = None
    # unconfirmed | confirmed
    lineage_state: str = "unconfirmed"
    lineage_confirmed_by: str | None = None
    lineage_confirmed_on: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    components: list[FormulaComponent] = field(default_factory=list)
    owner: str | None = None
    owner_confirmed_on: str | None = None
    change_summary: str | None = None


@dataclass
class Product(Serializable):
    id: str
    name: str = ""
    owner: str | None = None


@dataclass
class ProductVersion(Serializable):
    id: str
    product_id: str
    formula_version_id: str
    label: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    owner: str | None = None
    owner_confirmed_on: str | None = None


@dataclass
class MaterialLotUse(Serializable):
    """某商品版本在指定期间使用的原料批次/显式原料关联。"""

    id: str
    product_version_id: str
    material_id: str
    material_batch_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@dataclass
class SalesBatch(Serializable):
    id: str
    product_id: str
    product_version_id: str
    batch_code: str | None = None
    produced_on: str | None = None
    sell_from: str | None = None
    sell_to: str | None = None
    parent_sales_batch_id: str | None = None
    inheritance_state: str = "unconfirmed"
    inheritance_confirmed_by: str | None = None
    inheritance_confirmed_on: str | None = None
    owner: str | None = None


# 需求中的“产品批次”和“销售批次继承关系”使用同一模型。
ProductBatch = SalesBatch


@dataclass
class ClaimTextVersion(Serializable):
    revision: int
    text: str
    changed_by: str | None = None
    changed_on: str | None = None
    reason: str | None = None


@dataclass
class EvidenceRef(Serializable):
    experiment_id: str
    experiment_version: int
    stance: str = "supports"  # supports | refutes
    applicability_id: str | None = None
    # 导入旧表时版本可能是从快照补全，而非审批记录显式写明。
    version_explicit: bool = True
    note: str | None = None


@dataclass
class Claim(Serializable):
    id: str
    text_versions: list[ClaimTextVersion] = field(default_factory=list)
    current_revision: int = 1
    evidence_links: list[EvidenceRef] = field(default_factory=list)
    owner: str | None = None

    @property
    def text(self) -> str:
        for version in reversed(self.text_versions):
            if version.revision == self.current_revision:
                return version.text
        return self.text_versions[-1].text if self.text_versions else ""

    def get_text_version(self, revision: int | None = None) -> ClaimTextVersion | None:
        revision = revision or self.current_revision
        return next((item for item in self.text_versions if item.revision == revision), None)


@dataclass
class ApprovalScope(Serializable):
    product_ids: list[str] = field(default_factory=list)
    product_version_ids: list[str] = field(default_factory=list)
    formula_version_ids: list[str] = field(default_factory=list)
    sales_batch_ids: list[str] = field(default_factory=list)
    # 只有合规明确授予时才能为 True；空列表不自动解释成“全部”。
    all_product_versions: bool = False


@dataclass
class Approval(Serializable):
    id: str
    claim_id: str
    claim_revision: int
    reviewer: str
    # approved | rejected | revoked | in_review | expired
    state: str = "approved"
    decided_on: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    channels: list[str] = field(default_factory=list)
    scope: ApprovalScope = field(default_factory=ApprovalScope)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    note: str | None = None
    source_state: str | None = None


@dataclass
class Asset(Serializable):
    id: str
    name: str = ""
    # page | material | landing_page | video | brochure ...
    kind: str = "page"
    location: str | None = None
    owner: str | None = None


@dataclass
class Publication(Serializable):
    id: str
    asset_id: str
    claim_id: str
    approval_id: str
    channel: str
    published_on: str
    claim_revision: int | None = None
    product_id: str | None = None
    product_version_id: str | None = None
    formula_version_id: str | None = None
    sales_batch_id: str | None = None
    campaign_id: str | None = None
    # active | ended | removed
    state: str = "active"
    ended_on: str | None = None
    replaced_by_publication_id: str | None = None
    source: str | None = None
    # 不可变快照：发布时表述、审批范围、证据版本和配方上下文。
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class Campaign(Serializable):
    id: str
    name: str = ""
    started_on: str | None = None
    ended_on: str | None = None
    owner: str | None = None
    description: str | None = None


@dataclass
class ReviewCase(Serializable):
    id: str
    key: str
    code: str
    severity: str
    publication_id: str | None
    title: str
    message: str
    subject_type: str | None = None
    subject_id: str | None = None
    status: str = "open"  # open | resolved
    first_seen_on: str | None = None
    last_seen_on: str | None = None
    reopen_count: int = 0
    resolution: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AuditEntry(Serializable):
    on: str
    actor: str
    action: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryData(Serializable):
    """JSON 持久化根对象。"""

    version: int = 1
    papers: list[Paper] = field(default_factory=list)
    protocols: list[Protocol] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    material_batches: list[MaterialBatch] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    applicabilities: list[Applicability] = field(default_factory=list)
    formulas: list[FormulaVersion] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    product_versions: list[ProductVersion] = field(default_factory=list)
    material_lot_uses: list[MaterialLotUse] = field(default_factory=list)
    sales_batches: list[SalesBatch] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    publications: list[Publication] = field(default_factory=list)
    campaigns: list[Campaign] = field(default_factory=list)
    review_cases: list[ReviewCase] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
