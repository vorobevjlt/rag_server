from typing import Any

from ag_ui.core import (
    RunAgentInput,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.agents.simple_agent.agent import create_simple_rag_agent
from src.config.logging import get_logger
from src.rag.retrieval.utils import get_project_settings
from src.services.clerkAuth import get_current_user_clerk_id
from src.services.supabase import supabase

router = APIRouter(tags=["agentRoutes"])
logger = get_logger(__name__)


def require_project_owner(project_id: str, clerk_id: str) -> dict:
    result = (
        supabase.table("projects")
        .select("id, name, context")
        .eq("id", project_id)
        .eq("owner_clerk_id", clerk_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return result.data[0]


def ensure_project_chat(project: dict, clerk_id: str) -> None:
    supabase.table("users").upsert(
        {"clerk_id": clerk_id}, on_conflict="clerk_id"
    ).execute()

    chat_result = (
        supabase.table("chats")
        .select("id")
        .eq("id", project["id"])
        .eq("project_id", project["id"])
        .eq("clerk_id", clerk_id)
        .execute()
    )
    if not chat_result.data:
        supabase.table("chats").insert(
            {
                "id": project["id"],
                "title": project["name"],
                "project_id": project["id"],
                "clerk_id": clerk_id,
            }
        ).execute()


def message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            getattr(part, "text", "")
            for part in content
            if getattr(part, "type", None) == "text"
        )
    return str(content or "")


def persist_message(
    *,
    chat_id: str,
    clerk_id: str,
    role: str,
    content: str,
    trace_id: str,
    citations: list[dict] | None = None,
) -> None:
    if not content.strip():
        return
    existing = (
        supabase.table("messages")
        .select("id")
        .eq("trace_id", trace_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return
    supabase.table("messages").insert(
        {
            "chat_id": chat_id,
            "clerk_id": clerk_id,
            "role": role,
            "content": content,
            "citations": citations or [],
            "trace_id": trace_id,
        }
    ).execute()


@router.post("/project")
async def run_project_agent(
    input_data: RunAgentInput,
    request: Request,
    current_user_clerk_id: str = Depends(get_current_user_clerk_id),
):
    project_id = request.headers.get("x-project-id", "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="Project ID is required")

    project = require_project_owner(project_id, current_user_clerk_id)
    ensure_project_chat(project, current_user_clerk_id)
    settings = get_project_settings(project_id)

    graph = create_simple_rag_agent(
        project_id=project_id,
        answer_mode=settings.get("answer_mode", "combined"),
        rag_enabled=settings.get("rag_enabled", True),
    )
    request_agent = LangGraphAgent(
        name="default",
        description="Project-isolated assistant with optional RAG retrieval",
        graph=graph,
        emit_raw_events=False,
    )

    # The authenticated project is authoritative even if a client supplies a
    # different thread id in its AG-UI payload.
    scoped_input = input_data.model_copy(update={"thread_id": project_id})
    latest_user = next(
        (
            message
            for message in reversed(scoped_input.messages)
            if getattr(message, "role", None) == "user"
        ),
        None,
    )
    if latest_user is not None:
        persist_message(
            chat_id=project_id,
            clerk_id=current_user_clerk_id,
            role="user",
            content=message_text(latest_user),
            trace_id=f"agui:user:{latest_user.id}",
        )

    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        assistant_messages: dict[str, list[str]] = {}
        assistant_order: list[str] = []
        citations: list[dict] = []

        async for event in request_agent.run(scoped_input):
            if (
                isinstance(event, TextMessageStartEvent)
                and event.role == "assistant"
            ):
                assistant_messages.setdefault(event.message_id, [])
                assistant_order.append(event.message_id)
            elif isinstance(event, TextMessageContentEvent):
                if event.message_id in assistant_messages:
                    assistant_messages[event.message_id].append(event.delta)
            elif isinstance(event, StateSnapshotEvent):
                snapshot = event.snapshot
                if isinstance(snapshot, dict) and isinstance(
                    snapshot.get("citations"), list
                ):
                    citations = snapshot["citations"]

            yield encoder.encode(event)

        try:
            final_message_id = next(
                (
                    message_id
                    for message_id in reversed(assistant_order)
                    if "".join(assistant_messages.get(message_id, [])).strip()
                ),
                None,
            )
            if final_message_id:
                persist_message(
                    chat_id=project_id,
                    clerk_id=current_user_clerk_id,
                    role="assistant",
                    content="".join(assistant_messages[final_message_id]),
                    citations=citations,
                    trace_id=f"{scoped_input.run_id}:assistant:{final_message_id}",
                )
        except Exception as error:
            logger.error(
                "assistant_message_persistence_failed",
                project_id=project_id,
                run_id=scoped_input.run_id,
                error=str(error),
            )

    return StreamingResponse(
        event_generator(),
        media_type=encoder.get_content_type(),
    )


@router.get("/project/health")
def project_agent_health():
    return {"status": "ok", "agent": {"name": "default"}}
