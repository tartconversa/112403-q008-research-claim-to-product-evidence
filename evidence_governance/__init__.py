"""研发证据—审批—投放版本治理应用。

模块只使用 Python 标准库。核心入口是 :class:`evidence_governance.service.GovernanceService`。
"""

from .models import (
    Approval,
    ApprovalScope,
    Applicability,
    Asset,
    Campaign,
    Claim,
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
    ProductBatch,
    ProductVersion,
    Protocol,
    Publication,
    RegistryData,
    ReviewCase,
    SalesBatch,
)
from .service import GovernanceService
from .store import Registry

__all__ = [
    "Approval",
    "ApprovalScope",
    "Applicability",
    "Asset",
    "Campaign",
    "Claim",
    "EvidenceRef",
    "Experiment",
    "ExperimentVersion",
    "FormulaComponent",
    "FormulaVersion",
    "GovernanceService",
    "Material",
    "MaterialBatch",
    "MaterialLotUse",
    "Paper",
    "Product",
    "ProductBatch",
    "ProductVersion",
    "Protocol",
    "Publication",
    "Registry",
    "RegistryData",
    "ReviewCase",
    "SalesBatch",
]

__version__ = "1.0.0"
