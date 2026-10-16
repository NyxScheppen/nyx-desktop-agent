"""Questionnaire runner control flow without a real browser or LLM."""

# pyright: reportPrivateUsage=false
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import nyx.activity.browser_survey as browser_survey
from nyx.llm.client import LlmClient
from nyx.types import LLMOutput


def test_result_marker_excludes_instructions() -> None:
    assert browser_survey._result_visible(
        "https://example.com/instructions", "最后系统会生成结果"
    ) is False
    assert browser_survey._result_visible(
        "https://example.com/results", "结果：甲"
    ) is True


async def test_browser_survey_clicks_and_reports_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = Mock()
    page.url = "https://example.com/quiz"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    body = Mock()
    body.inner_text = AsyncMock(side_effect=["开始问卷", "结果：甲"])
    button = Mock()
    button.inner_text = AsyncMock(return_value="开始")
    button.get_attribute = AsyncMock(return_value=None)

    async def click(*, timeout: int) -> None:
        page.url = "https://example.com/results.html"

    button.click = AsyncMock(side_effect=click)
    controls = Mock()
    controls.count = AsyncMock(return_value=1)
    controls.nth.return_value = button
    def choose_locator(selector: str) -> Mock:
        return body if selector == "body" else controls

    page.locator.side_effect = choose_locator

    context = Mock()
    context.route = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = Mock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    chromium = Mock()
    chromium.launch = AsyncMock(return_value=browser)
    playwright = Mock(chromium=chromium)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=playwright)
    manager.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(browser_survey, "async_playwright", lambda: manager)
    def public_url(url: str) -> bool:
        return True

    monkeypatch.setattr(browser_survey, "_is_safe_url", public_url)

    llm = Mock()
    llm.complete = AsyncMock(
        side_effect=[
            LLMOutput("activity", "browser_survey", "fake", '{"index":0}', "task"),
            LLMOutput(
                "activity", "browser_survey", "fake",
                '{"done":true,"summary":"测试结果"}', "task",
            ),
        ]
    )
    saved: list[int] = []

    async def save_progress(answers: int) -> None:
        saved.append(answers)

    result = await browser_survey.run_browser_survey(
        "https://example.com/quiz", "角色设定", cast(LlmClient, llm),
        "task", save_progress,
    )
    assert result["summary"] == "测试结果"
    assert saved == [1]
    assert button.click.await_count == 1
    assert browser.close.await_count == 1


async def test_browser_survey_rejects_private_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def private_url(url: str) -> bool:
        return False

    monkeypatch.setattr(browser_survey, "_is_safe_url", private_url)

    async def save_progress(answers: int) -> None:
        pass

    with pytest.raises(ValueError, match="公网"):
        await browser_survey.run_browser_survey(
            "http://127.0.0.1/", "角色设定", cast(LlmClient, cast(Any, object())),
            "task", save_progress,
        )
