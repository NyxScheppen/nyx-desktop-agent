from nyx.enums import AssignedTaskStatus, AssignedTaskType
from nyx.tools.activity_tasks import build_activity_task_tools
from nyx.tools.browser_survey import build_browser_survey_tool
from nyx.types import AssignedTask


async def test_browser_survey_tool_only_queues_task() -> None:
    urls: list[str] = []

    async def assign(url: str) -> AssignedTask:
        urls.append(url)
        return AssignedTask(
            "task-1", AssignedTaskType.BROWSER_SURVEY,
            AssignedTaskStatus.PENDING, url, None, None, {}, None, 1.0, 1.0,
        )

    tool = build_browser_survey_tool(assign)
    result = await tool.handler(url="https://example.com/quiz")
    assert urls == ["https://example.com/quiz"]
    assert result == {
        "task_id": "task-1", "status": "pending", "url": "https://example.com/quiz"
    }


async def test_activity_task_tools_share_assignment_callbacks() -> None:
    calls: list[tuple[str, object]] = []

    async def assign_web(value: str) -> AssignedTask:
        calls.append(("web", value))
        return AssignedTask(
            "web-1", AssignedTaskType.WEB, AssignedTaskStatus.PENDING,
            value, None, None, {}, None, 1.0, 1.0,
        )

    async def assign_book(title: str, paragraph: int) -> AssignedTask:
        calls.append(("book", (title, paragraph)))
        return AssignedTask(
            "book-1", AssignedTaskType.BOOK, AssignedTaskStatus.PENDING,
            None, "book-1", paragraph, {"title": title}, None, 1.0, 1.0,
        )

    async def assign_creation(description: str) -> AssignedTask:
        calls.append(("creation", description))
        return AssignedTask(
            "creation-1", AssignedTaskType.CREATION, AssignedTaskStatus.PENDING,
            None, None, None, {"description": description}, None, 1.0, 1.0,
        )

    tools = build_activity_task_tools(
        assign_web, assign_book, assign_creation, assign_web
    )
    await tools[0].handler(url="https://example.com/article")
    await tools[1].handler(title="某书", target_paragraph=3)
    await tools[2].handler(description="写一段关于雨的随笔")
    assert calls == [
        ("web", "https://example.com/article"),
        ("book", ("某书", 3)),
        ("creation", "写一段关于雨的随笔"),
    ]
