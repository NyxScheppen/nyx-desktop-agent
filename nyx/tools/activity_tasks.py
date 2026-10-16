"""Conversation tools for user-directed activity tasks."""

from collections.abc import Awaitable, Callable
from typing import Any

from nyx.types import AssignedTask, Tool


def _task_result(task: AssignedTask) -> dict[str, Any]:
    return {"task_id": task.id, "status": task.status.value}


def build_activity_task_tools(
    assign_web: Callable[[str], Awaitable[AssignedTask]],
    assign_book: Callable[[str, int], Awaitable[AssignedTask]],
    assign_creation: Callable[[str], Awaitable[AssignedTask]],
    assign_survey: Callable[[str], Awaitable[AssignedTask]],
) -> tuple[Tool, ...]:
    """Build all user-directed task tools against one ActivityFacade."""

    async def web_task(url: str) -> dict[str, Any]:
        task = await assign_web(url)
        return {**_task_result(task), "url": task.url}

    async def book_task(
        title: str, target_paragraph: int
    ) -> dict[str, Any]:
        task = await assign_book(title, target_paragraph)
        return {**_task_result(task), "book_id": task.book_id}

    async def creation_task(description: str) -> dict[str, Any]:
        task = await assign_creation(description)
        return {**_task_result(task), "description": task.checkpoint.get("description")}

    async def browser_survey(url: str) -> dict[str, Any]:
        task = await assign_survey(url)
        return {**_task_result(task), "url": task.url}

    return (
        Tool(
            name="web_task",
            description=(
                "用户明确要求 Nyx 阅读或了解指定公开网页时，提交后台网页阅读任务。"
            ),
            schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handler=web_task,
        ),
        Tool(
            name="book_task",
            description=(
                "用户明确要求 Nyx 阅读书库中的某本书到指定段落时，提交读书任务。"
            ),
            schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "target_paragraph": {"type": "integer", "minimum": 1},
                },
                "required": ["title", "target_paragraph"],
            },
            handler=book_task,
        ),
        Tool(
            name="creation_task",
            description="用户明确要求 Nyx 创作文字时，提交后台创作任务。",
            schema={
                "type": "object",
                "properties": {"description": {"type": "string"}},
                "required": ["description"],
            },
            handler=creation_task,
        ),
        Tool(
            name="browser_survey",
            description=(
                "用户明确要求 Nyx 亲自填写公开网页问卷时，提交后台浏览器问卷任务。"
            ),
            schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            handler=browser_survey,
        ),
    )
