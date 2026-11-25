"""通用错误、日期和计量工具。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable


class GovernanceError(ValueError):
    """业务规则或输入引用不合法。"""

    code = "governance_error"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(GovernanceError):
    code = "not_found"


class AuthorizationRuleError(GovernanceError):
    code = "authorization_rule"


def parse_date(value: str | date | None, *, field_name: str = "date") -> date | None:
    """解析 ISO 日期；空值返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise GovernanceError(f"{field_name} 必须是 YYYY-MM-DD 日期：{value!r}", code="invalid_date") from exc


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def as_date(value: str | date | None) -> date | None:
    return parse_date(value)


def date_within(value: date, start: date | None, end: date | None) -> bool:
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


_STRENGTH_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([^\d\s].*?)?\s*$")


def parse_strength(value: Any) -> tuple[Decimal, str] | None:
    """把 ``"2%"`` / ``"20 mg/g"`` 解析为可比较的数值和单位。"""
    if value is None or value == "":
        return None
    text = str(value).strip().replace("％", "%")
    match = _STRENGTH_RE.match(text)
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        return None
    unit = (match.group(2) or "").strip().lower().replace(" ", "")
    return number, unit


def strength_matches(observed: Any, required: Any) -> bool | None:
    """返回 True/False；单位缺失或量纲不可可靠换算时返回 None（证据不足）。"""
    left = parse_strength(observed)
    right = parse_strength(required)
    if left is None or right is None:
        return None
    left_number, left_unit = left
    right_number, right_unit = right
    if left_unit != right_unit:
        # 例如 % 与 mg/g 可能是同一浓度的不同表达，但需要密度/换算上下文；
        # 不能在本模块中武断判为不匹配。
        return None
    return left_number == right_number


def uniq(values: Iterable[Any]) -> list[Any]:
    seen: list[Any] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "blocker"  # blocker | gap | warning
    subject_type: str | None = None
    subject_id: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "details": self.details or {},
        }


def issues_to_dicts(issues: Iterable[Issue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]
