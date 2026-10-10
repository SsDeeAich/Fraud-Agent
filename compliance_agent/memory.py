"""Memory Bank service and utilities for the Impact Compliance Agent.

Supports:
1. Vertex AI Agent Platform Memory Bank (VertexAiMemoryBankService) for cloud deployment.
2. InMemoryMemoryService for local development, testing, and offline evaluation.
"""

import datetime
import logging
import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
from google.adk.memory.base_memory_service import BaseMemoryService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.genai import types

load_dotenv()
logger = logging.getLogger("compliance_agent.memory")

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "customers-444618")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
DEFAULT_ENGINE_ID = os.getenv("AGENT_ENGINE_ID", "4135651909425954816")
APP_NAME = "compliance_agent"


def get_memory_service(
    agent_engine_id: Optional[str] = None,
    force_in_memory: bool = False,
) -> BaseMemoryService:
    """Instantiates and returns the appropriate MemoryService.

    Args:
        agent_engine_id: Vertex AI Agent Engine / Reasoning Engine ID.
        force_in_memory: If True, uses the local InMemoryMemoryService.

    Returns:
        A BaseMemoryService instance (VertexAiMemoryBankService or InMemoryMemoryService).
    """
    engine_id = agent_engine_id or os.getenv("AGENT_ENGINE_ID", DEFAULT_ENGINE_ID)

    if force_in_memory:
        logger.info("Using local InMemoryMemoryService.")
        return InMemoryMemoryService()

    try:
        logger.info(
            "Connecting to Vertex AI Agent Platform Memory Bank (project=%s, location=%s, engine_id=%s)",
            PROJECT_ID,
            LOCATION,
            engine_id,
        )
        return VertexAiMemoryBankService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=engine_id,
        )
    except Exception as e:
        logger.warning(
            "Failed to initialize VertexAiMemoryBankService: %s. Falling back to InMemoryMemoryService.",
            e,
        )
        return InMemoryMemoryService()


async def add_compliance_note(
    publisher_id: int,
    note: str,
    user_id: str = "compliance_analyst",
    memory_service: Optional[BaseMemoryService] = None,
    custom_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Stores an investigator compliance finding or note into the Memory Bank.

    Args:
        publisher_id: Target publisher account ID.
        note: Narrative text or findings to persist across future sessions.
        user_id: Analyst identifier scope.
        memory_service: Optional memory service instance.
        custom_metadata: Additional metadata tags.
    """
    service = memory_service or get_memory_service()
    fact_text = f"[Publisher ID: {publisher_id}] {note.strip()}"

    entry = MemoryEntry(
        author=user_id,
        content=types.Content(
            role="user",
            parts=[types.Part.from_text(text=fact_text)],
        ),
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    try:
        # Try add_memory first (supported natively by VertexAiMemoryBankService)
        await service.add_memory(
            app_name=APP_NAME,
            user_id=user_id,
            memories=[entry],
            custom_metadata=custom_metadata,
        )
        logger.info("Stored compliance note into Memory Bank for publisher %s", publisher_id)
    except (NotImplementedError, AttributeError):
        # Fallback to add_events_to_memory (used by InMemoryMemoryService and event streams)
        from google.adk.events import Event
        event = Event(
            author=user_id,
            content=entry.content,
        )
        await service.add_events_to_memory(
            app_name=APP_NAME,
            user_id=user_id,
            events=[event],
            custom_metadata=custom_metadata,
        )
        logger.info("Stored compliance note via event into Memory Bank for publisher %s", publisher_id)
    except Exception as e:
        logger.error("Failed to store compliance note into Memory Bank: %s", e)
        raise


async def search_compliance_memory(
    query: str,
    user_id: str = "compliance_analyst",
    memory_service: Optional[BaseMemoryService] = None,
) -> List[str]:
    """Queries the Memory Bank for relevant past investigator notes.

    Args:
        query: Search query string (e.g. publisher ID, company name, violation type).
        user_id: Analyst identifier scope.
        memory_service: Optional memory service instance.

    Returns:
        List of matching memory text snippets.
    """
    service = memory_service or get_memory_service()
    try:
        response = await service.search_memory(
            app_name=APP_NAME,
            user_id=user_id,
            query=query,
        )
        results = []
        for mem in response.memories:
            if mem.content and mem.content.parts:
                for part in mem.content.parts:
                    if hasattr(part, "text") and part.text:
                        results.append(part.text)
        logger.info("Found %d memories matching query '%s'", len(results), query)
        return results
    except Exception as e:
        logger.error("Error querying compliance memory: %s", e)
        return []
