"""Authenticated read-only feedback analytics endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...analytics import AnalyticsFilter, FeedbackAnalyticsService
from .. import deps
from ..deps import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_CURRENT_USER = Annotated[dict, Depends(get_current_user)]


def _validate_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    if (start is None) != (end is None):
        raise HTTPException(status_code=422, detail="Invalid date range")
    if start is None or end is None:
        return None, None
    if any(
        len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or not (value[:4] + value[5:7] + value[8:]).isdigit()
        for value in (start, end)
    ):
        raise HTTPException(status_code=422, detail="Invalid date range")
    try:
        parsed_start = date.fromisoformat(start)
        parsed_end = date.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid date range") from exc
    if parsed_end < parsed_start:
        raise HTTPException(status_code=422, detail="Invalid date range")
    return parsed_start.isoformat(), parsed_end.isoformat()


def analytics_filter_dependency(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    compare_from: str | None = Query(None),
    compare_to: str | None = Query(None),
) -> AnalyticsFilter:
    """Parse complete inclusive ISO date ranges for analytics queries."""
    date_from, date_to = _validate_range(date_from, date_to)
    compare_from, compare_to = _validate_range(compare_from, compare_to)
    return AnalyticsFilter(
        date_from=date_from,
        date_to=date_to,
        compare_from=compare_from,
        compare_to=compare_to,
    )


def _service() -> FeedbackAnalyticsService:
    repository = deps.get_feedback_analytics_repository()
    if repository is None:
        raise HTTPException(status_code=503, detail="Feedback analytics repository is unavailable")
    return FeedbackAnalyticsService(repository)


@router.get("/overview")
def overview(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().overview(analytics_filter)


@router.get("/sources")
def sources(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().sources(analytics_filter)


@router.get("/units")
def units(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().units(analytics_filter)


@router.get("/groups")
def groups(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().groups(analytics_filter)


@router.get("/products")
def products(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().products(analytics_filter)


@router.get("/issues")
def issues(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    source: str | None = Query(None),
    unit_name: str | None = Query(None, alias="unit"),
    label: str | None = Query(None),
    product: str | None = Query(None),
    business_status: str | None = Query(None, alias="status"),
):
    return _service().issues(
        analytics_filter,
        page=page,
        page_size=page_size,
        source=source,
        unit_name=unit_name,
        label=label,
        product=product,
        business_status=business_status,
    )


@router.get("/data-quality")
def data_quality(
    user: _CURRENT_USER,
    analytics_filter: AnalyticsFilter = Depends(analytics_filter_dependency),
):
    return _service().data_quality(analytics_filter)
