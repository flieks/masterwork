"""Data access for simulations."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.simulation import Simulation


async def create_simulation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    scenario: str,
    control_run: bool = False,
    autopilot_run_id: uuid.UUID | None = None,
    autopilot_iteration: int | None = None,
    autopilot_total: int | None = None,
) -> Simulation:
    simulation = Simulation(
        project_id=project_id,
        status="running",
        scenario=scenario,
        suggestions=[],
        control_run=control_run,
        autopilot_run_id=autopilot_run_id,
        autopilot_iteration=autopilot_iteration,
        autopilot_total=autopilot_total,
    )
    db.add(simulation)
    await db.flush()
    await db.refresh(simulation)
    return simulation


async def list_simulations(db: AsyncSession, project_id: uuid.UUID) -> list[Simulation]:
    result = await db.execute(
        select(Simulation)
        .where(Simulation.project_id == project_id)
        .order_by(Simulation.created_at.desc())
    )
    return list(result.scalars().all())


async def get_simulation(db: AsyncSession, simulation_id: uuid.UUID) -> Simulation | None:
    return await db.get(Simulation, simulation_id)


async def latest_completed_for_scenario(
    db: AsyncSession,
    project_id: uuid.UUID,
    scenario: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> Simulation | None:
    """Most recent completed run of the SAME scenario — the memory a new run
    re-grades its checklist against. Same scenario text = comparable checklist."""
    stmt = select(Simulation).where(
        Simulation.project_id == project_id,
        Simulation.scenario == scenario,
        Simulation.status == "completed",
    )
    if exclude_id is not None:
        stmt = stmt.where(Simulation.id != exclude_id)
    stmt = stmt.order_by(Simulation.created_at.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def project_has_running(db: AsyncSession, project_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Simulation.id)
        .where(Simulation.project_id == project_id, Simulation.status == "running")
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def autopilot_has_running(db: AsyncSession, run_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(Simulation.id)
        .where(Simulation.autopilot_run_id == run_id, Simulation.status == "running")
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def delete_simulation(db: AsyncSession, simulation: Simulation) -> None:
    await db.delete(simulation)
    await db.flush()


async def interrupt_all_running(db: AsyncSession, *, error: str) -> None:
    """Startup sweep: a backend restart orphans any in-flight run.

    These are marked `interrupted`, not `failed` — nothing is wrong with the
    user's assets, the process just went away underneath the run.
    """
    await db.execute(
        update(Simulation)
        .where(Simulation.status == "running")
        .values(status="interrupted", error=error)
    )


async def latest_completed_for_project(
    db: AsyncSession, project_id: uuid.UUID
) -> Simulation | None:
    """Most recent completed run regardless of scenario — the baseline for
    cross-project change alerts ("did anything change since I last scored?")."""
    result = await db.execute(
        select(Simulation)
        .where(Simulation.project_id == project_id, Simulation.status == "completed")
        .order_by(Simulation.completed_at.desc().nulls_last())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_completed_excluding_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[Simulation]:
    """Completed runs of OTHER projects — their applied suggestions may have
    modified assets this project links."""
    result = await db.execute(
        select(Simulation).where(
            Simulation.project_id != project_id, Simulation.status == "completed"
        )
    )
    return list(result.scalars().all())
