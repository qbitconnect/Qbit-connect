"""AI Orchestration Worker.

Polls AIAgentRun records with status=QUEUED and dispatches them to the
appropriate agent. Integrated with the existing background worker infrastructure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.models.ai import AgentRunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.core.config import Settings
    from app.db.session import DatabaseManager

logger = get_logger("qbit.ai.orchestrator")


class AIOrchestrationWorker:
    """Background worker that processes queued AI agent runs."""

    def __init__(self, settings: "Settings", db: "DatabaseManager") -> None:
        self.settings = settings
        self.db = db
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run_once(self) -> int:
        """Process all queued agent runs. Returns count processed."""
        from sqlalchemy import select
        from app.models.ai import AIAgentRun
        from app.services.ai.gateway import get_gateway
        from app.services.ai.agents import get_agent

        gateway = get_gateway(self.settings)
        processed = 0

        async with self.db.session() as session:
            result = await session.execute(
                select(AIAgentRun)
                .where(AIAgentRun.status == AgentRunStatus.QUEUED.value)
                .limit(10)
            )
            runs = result.scalars().all()

            for run in runs:
                try:
                    agent_slug = run.task_name.replace("ai_agent:", "", 1)
                    agent = get_agent(agent_slug, gateway, self.settings)

                    run.status = AgentRunStatus.RUNNING.value
                    await session.flush()

                    output = await agent.run(session, run)

                    run.status = AgentRunStatus.COMPLETED.value
                    run.output_data = output
                    run.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    processed += 1

                except Exception as exc:  # noqa: BLE001
                    logger.exception("AI agent run %s failed", run.id)
                    try:
                        run.status = AgentRunStatus.FAILED.value
                        run.error_message = str(exc)
                        run.completed_at = datetime.now(timezone.utc)
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        await session.rollback()

        return processed

    async def run(self) -> None:
        """Continuous poll loop (called from lifespan or standalone process)."""
        self._running = True
        logger.info("AI orchestration worker started")
        while self._running:
            try:
                count = await self.run_once()
                if count > 0:
                    logger.info("AI orchestrator processed %d runs", count)
            except Exception:  # noqa: BLE001
                logger.exception("AI orchestrator poll error")
            await asyncio.sleep(5.0)
        logger.info("AI orchestration worker stopped")
