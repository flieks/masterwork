"""Simulation endpoints: project-scoped run/list, plus per-simulation
get/delete and suggestion apply.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_db, get_providers, get_session_factory, get_simulation_runner
from app.api.v1.simulations import schemas, service
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner

router = APIRouter(tags=["simulations"])


@router.get(
    "/projects/{project_id}/simulations",
    response_model=list[schemas.Simulation],
    operation_id="listSimulations",
)
async def list_simulations(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[schemas.Simulation]:
    return await service.list_simulations(db, project_id)


@router.post(
    "/projects/{project_id}/simulations",
    response_model=schemas.Simulation,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createSimulation",
)
async def create_simulation(
    project_id: str,
    body: schemas.SimulationCreateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    providers: list[Provider] = Depends(get_providers),
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.Simulation:
    return await service.start_simulation(
        db, session_factory, providers, runner, background, project_id, body
    )


@router.post(
    "/projects/{project_id}/simulations/autopilot",
    response_model=schemas.Simulation,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="startSimulationAutopilot",
)
async def start_autopilot(
    project_id: str,
    body: schemas.AutopilotCreateRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    providers: list[Provider] = Depends(get_providers),
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.Simulation:
    return await service.start_autopilot(
        db, session_factory, providers, runner, background, project_id, body
    )


@router.post(
    "/simulations/autopilot/{run_id}/stop",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="stopSimulationAutopilot",
)
async def stop_autopilot(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.stop_autopilot(db, run_id)


@router.post(
    "/projects/{project_id}/simulations/scenario",
    response_model=schemas.ScenarioGenerateResponse,
    operation_id="generateSimulationScenario",
)
async def generate_scenario(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.ScenarioGenerateResponse:
    return await service.generate_scenario(db, providers, runner, project_id)


@router.get(
    "/simulations/{simulation_id}",
    response_model=schemas.Simulation,
    operation_id="getSimulation",
)
async def get_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
) -> schemas.Simulation:
    return await service.get_simulation(db, simulation_id)


@router.delete(
    "/simulations/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteSimulation",
)
async def delete_simulation(
    simulation_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_simulation(db, simulation_id)


@router.post(
    "/simulations/{simulation_id}/suggestions/{suggestion_index}/apply",
    response_model=schemas.Simulation,
    operation_id="applySimulationSuggestion",
)
async def apply_suggestion(
    simulation_id: str,
    suggestion_index: int,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
) -> schemas.Simulation:
    return await service.apply_suggestion(db, providers, simulation_id, suggestion_index)
