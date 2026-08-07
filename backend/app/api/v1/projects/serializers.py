"""Map Project ORM rows to the contract's Pydantic schemas."""

from __future__ import annotations

from app.api.v1.projects import schemas
from app.db.models.project import Project


def project_to_schema(project: Project) -> schemas.Project:
    return schemas.Project(
        id=str(project.id),
        name=project.name,
        goal=project.goal,
        flow_mermaid=project.flow_mermaid,
        asset_ids=list(project.asset_ids),
        scenario=project.scenario,
        change_summary=project.change_summary,
        change_summary_at=project.change_summary_at,
        trigger_guide=project.trigger_guide,
        trigger_guide_at=project.trigger_guide_at,
        generality_report=project.generality_report,
        generality_report_at=project.generality_report_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
