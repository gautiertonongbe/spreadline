"""Bulk analysis and export (spec §28)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Response, UploadFile

from app.api.deps import Auth, DbSession, Providers
from app.core.errors import ValidationError
from app.domains.opportunities.analysis import AnalysisOptions
from app.domains.opportunities.bulk import MAX_ROWS, parse_csv, run_bulk_analysis
from app.schemas.analysis import BulkAnalyzeRequest

router = APIRouter(prefix="/bulk", tags=["bulk"])

#: Upload ceiling. A CSV of identifiers is small; anything larger is a mistake.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


@router.post("/analyze")
async def bulk_analyze(
    payload: BulkAnalyzeRequest, session: DbSession, auth: Auth, providers: Providers
) -> dict[str, Any]:
    """Analyse a pasted CSV of identifiers or marketplace URLs."""
    rows = parse_csv(payload.content)
    result = await run_bulk_analysis(
        session,
        auth,
        rows,
        default_source=payload.default_source_marketplace,
        default_target=payload.default_target_marketplace,
        options=AnalysisOptions(run_stress_test=payload.run_stress_test),
        registry=providers,
    )
    return result.as_dict()


@router.post("/analyze/upload")
async def bulk_analyze_upload(
    session: DbSession,
    auth: Auth,
    providers: Providers,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is {len(content) // 1024} KB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024} KB (roughly {MAX_ROWS} rows of identifiers)."
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("The file must be UTF-8 encoded CSV or plain text.") from exc

    rows = parse_csv(text)
    result = await run_bulk_analysis(
        session, auth, rows, options=AnalysisOptions(run_stress_test=False), registry=providers
    )
    return result.as_dict()


@router.post("/analyze/export", response_class=Response)
async def bulk_analyze_export(
    payload: BulkAnalyzeRequest, session: DbSession, auth: Auth, providers: Providers
) -> Response:
    """Run a bulk analysis and return the ranked results as CSV."""
    rows = parse_csv(payload.content)
    result = await run_bulk_analysis(
        session,
        auth,
        rows,
        default_source=payload.default_source_marketplace,
        default_target=payload.default_target_marketplace,
        options=AnalysisOptions(run_stress_test=False),
        registry=providers,
    )
    return Response(
        content=result.to_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="spreadline-bulk-analysis.csv"'},
    )
