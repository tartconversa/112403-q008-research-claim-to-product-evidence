"""内存登记处与 JSON 持久化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Approval,
    Applicability,
    Asset,
    AuditEntry,
    Campaign,
    Claim,
    Experiment,
    FormulaVersion,
    Material,
    MaterialBatch,
    MaterialLotUse,
    Paper,
    Product,
    ProductVersion,
    Protocol,
    Publication,
    RegistryData,
    ReviewCase,
    SalesBatch,
)
from .utils import GovernanceError, NotFoundError


class Registry:
    """所有显式登记实体的集合。

    业务方法在 :class:`evidence_governance.service.GovernanceService`；
    本类只负责引用存取和序列化。
    """

    def __init__(self, data: RegistryData | None = None):
        self.data = data or RegistryData()

    # ---- 通用集合操作 -------------------------------------------------
    def _map(self, name: str) -> dict[str, Any]:
        return {item.id: item for item in getattr(self.data, name)}

    @property
    def papers(self) -> dict[str, Paper]:
        return self._map("papers")

    @property
    def protocols(self) -> dict[str, Protocol]:
        return self._map("protocols")

    @property
    def materials(self) -> dict[str, Material]:
        return self._map("materials")

    @property
    def material_batches(self) -> dict[str, MaterialBatch]:
        return self._map("material_batches")

    @property
    def experiments(self) -> dict[str, Experiment]:
        return self._map("experiments")

    @property
    def applicabilities(self) -> dict[str, Applicability]:
        return self._map("applicabilities")

    @property
    def formulas(self) -> dict[str, FormulaVersion]:
        return self._map("formulas")

    @property
    def products(self) -> dict[str, Product]:
        return self._map("products")

    @property
    def product_versions(self) -> dict[str, ProductVersion]:
        return self._map("product_versions")

    @property
    def material_lot_uses(self) -> dict[str, MaterialLotUse]:
        return self._map("material_lot_uses")

    @property
    def sales_batches(self) -> dict[str, SalesBatch]:
        return self._map("sales_batches")

    @property
    def claims(self) -> dict[str, Claim]:
        return self._map("claims")

    @property
    def approvals(self) -> dict[str, Approval]:
        return self._map("approvals")

    @property
    def assets(self) -> dict[str, Asset]:
        return self._map("assets")

    @property
    def publications(self) -> dict[str, Publication]:
        return self._map("publications")

    @property
    def campaigns(self) -> dict[str, Campaign]:
        return self._map("campaigns")

    @property
    def review_cases(self) -> dict[str, ReviewCase]:
        return self._map("review_cases")

    def add(self, collection: str, item: Any, *, replace: bool = False) -> Any:
        rows: list[Any] = getattr(self.data, collection)
        existing = next((row for row in rows if row.id == item.id), None)
        if existing is not None:
            if not replace:
                raise GovernanceError(f"{collection} 中已存在 ID={item.id!r}", code="duplicate_id")
            index = rows.index(existing)
            rows[index] = item
        else:
            rows.append(item)
        return item

    def get(self, collection: str, item_id: str) -> Any:
        item = self._map(collection).get(item_id)
        if item is None:
            raise NotFoundError(f"{collection} 中不存在 ID={item_id!r}", details={"id": item_id})
        return item

    def find(self, collection: str, item_id: str) -> Any | None:
        return self._map(collection).get(item_id)

    def next_id(self, prefix: str) -> str:
        current = self.data.counters.get(prefix, 0) + 1
        self.data.counters[prefix] = current
        return f"{prefix}-{current:04d}"

    def audit(self, on: str, actor: str, action: str, **details: Any) -> None:
        self.data.audit.append(AuditEntry(on=on, actor=actor, action=action, details=details))

    # ---- JSON ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return self.data.to_dict()

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Registry":
        return cls(RegistryData.from_dict(payload))

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        source = Path(path)
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    @classmethod
    def load_or_empty(cls, path: str | Path | None) -> "Registry":
        if path is None:
            return cls()
        source = Path(path)
        if not source.exists():
            return cls()
        return cls.load(source)

    def save_if_path(self, path: str | Path | None) -> None:
        if path is not None:
            self.save(path)

    def replace_review_case(self, case: ReviewCase) -> None:
        self.add("review_cases", case, replace=True)

    def all_approvals_for_claim(self, claim_id: str) -> Iterable[Approval]:
        return [approval for approval in self.data.approvals if approval.claim_id == claim_id]
