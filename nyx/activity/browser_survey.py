"""Run a user assigned public questionnaire in a visible browser."""

# pyright: reportPrivateUsage=false

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.async_api import Page, Route, async_playwright

from nyx.llm.client import LlmClient
from nyx.policy import policy
from nyx.tools.web_fetch import _is_safe_url

_CONTROLS = (
    policy("activity", "browser_survey._CONTROLS", str)
)
_MAX_ACTIONS = policy("activity", "browser_survey._MAX_ACTIONS", int)


def _result_visible(url: str, body: str) -> bool:
    if "result" in urlsplit(url).path.casefold():
        return True
    heading = body[
        : policy(
            "activity",
            "browser_survey.parameters.result_visible_heading_chars_or_items",
            int,
        )
    ].casefold()
    return (
        any(
            marker in heading
            for marker in policy(
                "activity", "browser_survey.copy.result_visible_words", tuple[str, ...]
            )
        )
        and policy("activity", "browser_survey.copy.result_visible_text", str)
        not in heading
    )


async def _public_route(route: Route) -> None:
    url = route.request.url
    if url.startswith(("data:", "blob:")) or await asyncio.to_thread(
        _is_safe_url, url
    ):
        await route.continue_()
    else:
        await route.abort()


async def _choices(page: Page, origin: str) -> list[tuple[int, str]]:
    controls = page.locator(_CONTROLS)
    found: list[tuple[int, str]] = []
    for index in range(min(await controls.count(), 60)):
        control = controls.nth(index)
        href = await control.get_attribute("href")
        if href and urlsplit(href).netloc not in ("", origin):
            continue
        label = (await control.inner_text()).strip()
        if not label:
            label = (await control.get_attribute("aria-label") or "").strip()
        if not label:
            label = (await control.get_attribute("value") or "").strip()
        if label and not any(
            word in label.casefold()
            for word in policy(
                "activity", "browser_survey.copy.choices_words", tuple[str, ...]
            )
        ):
            found.append(
                (
                    index,
                    label[
                        : policy(
                            "activity",
                            "browser_survey.parameters.choices_value_chars_or_items",
                            int,
                        )
                    ],
                )
            )
    return found


async def run_browser_survey(
    url: str,
    canon: str,
    llm: LlmClient,
    task_id: str,
    save_progress: Callable[[int], Awaitable[None]],
) -> dict[str, Any]:
    """Choose questionnaire options as Nyx; never replay a partly clicked session."""
    if not await asyncio.to_thread(_is_safe_url, url):
        raise ValueError(
            policy("activity", "browser_survey.copy.run_browser_survey_text", str)
        )
    origin = urlsplit(url).netloc
    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(
                    headless=False,
                    channel="msedge" if sys.platform == "win32" else None,
                )
            except Exception:
                browser = await playwright.chromium.launch(headless=False)
            try:
                context = await browser.new_context()
                await context.route("**/*", _public_route)
                page = await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=policy(
                        "activity",
                        "browser_survey.parameters.run_browser_survey_timeout",
                        int,
                    ),
                )
                for step in range(_MAX_ACTIONS):
                    deadline = time.monotonic() + policy(
                        "activity",
                        "browser_survey.parameters.run_browser_survey_deadline_budget",
                        int,
                    )
                    while True:
                        body = (await page.locator("body").inner_text())[
                            : policy(
                                "activity",
                                "browser_survey.parameters.run_browser_survey_body_chars_or_items",
                                int,
                            )
                        ]
                        choices = await _choices(page, origin)
                        if body.strip() and (
                            choices or _result_visible(page.url, body)
                        ):
                            break
                        if time.monotonic() >= deadline:
                            raise ValueError(
                                policy(
                                    "activity",
                                    "browser_survey.copy.run_browser_survey_text_6",
                                    str,
                                )
                            )
                        await page.wait_for_timeout(
                            policy(
                                "activity",
                                "browser_survey.parameters.run_browser_survey_wait_for_timeout",
                                int,
                            )
                        )
                    observed = {"page": body, "choices": choices, "url": page.url}
                    output = await llm.complete(
                        [
                            {
                                "role": "system",
                                "content": (
                                    canon
                                    + policy(
                                        "activity",
                                        "browser_survey.parameters.run_browser_survey_content_text",
                                        str,
                                    )
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(observed, ensure_ascii=False),
                            },
                        ],
                        module="activity",
                        output_type="browser_survey",
                        correlation_id=task_id,
                        json_mode=True,
                    )
                    parsed = json.loads(output.content)
                    if not isinstance(parsed, dict):
                        raise ValueError(
                            policy(
                                "activity",
                                "browser_survey.copy.run_browser_survey_text_3",
                                str,
                            )
                        )
                    decision = cast(dict[str, object], parsed)
                    error = decision.get("error")
                    if isinstance(error, str) and error:
                        raise ValueError(
                            error[
                                : policy(
                                    "activity",
                                    "browser_survey.parameters.run_browser_survey_value_chars_or_items",
                                    int,
                                )
                            ]
                        )
                    if decision.get("done") is True:
                        summary = decision.get("summary")
                        if not isinstance(summary, str) or not summary.strip():
                            raise ValueError(
                                policy(
                                    "activity",
                                    "browser_survey.copy.run_browser_survey_text_7",
                                    str,
                                )
                            )
                        if step == 0 or not _result_visible(page.url, body):
                            raise ValueError(
                                policy(
                                    "activity",
                                    "browser_survey.copy.run_browser_survey_text_8",
                                    str,
                                )
                            )
                        return {
                            "url": page.url,
                            "summary": summary[
                                : policy(
                                    "activity",
                                    "browser_survey.parameters.run_browser_survey_summary_chars_or_items",
                                    int,
                                )
                            ],
                            "result_text": body[
                                : policy(
                                    "activity",
                                    "browser_survey.parameters.run_browser_survey_result_text_chars_or_items",
                                    int,
                                )
                            ],
                            "steps": step,
                        }
                    index = decision.get("index")
                    allowed = {item[0] for item in choices}
                    if (
                        not isinstance(index, int)
                        or isinstance(index, bool)
                        or index not in allowed
                    ):
                        raise ValueError(
                            policy(
                                "activity",
                                "browser_survey.copy.run_browser_survey_text_4",
                                str,
                            )
                        )
                    await (
                        page.locator(_CONTROLS)
                        .nth(index)
                        .click(
                            timeout=policy(
                                "activity",
                                "browser_survey.parameters.run_browser_survey_timeout_2",
                                int,
                            )
                        )
                    )
                    if urlsplit(page.url).netloc != origin:
                        raise ValueError(
                            policy(
                                "activity",
                                "browser_survey.copy.run_browser_survey_text_5",
                                str,
                            )
                        )
                    await save_progress(step + 1)
                raise ValueError(
                    policy(
                        "activity", "browser_survey.copy.run_browser_survey_text_2", str
                    )
                )
            finally:
                await browser.close()
    except Exception as error:
        if "Executable doesn't exist" in str(error):
            raise RuntimeError(
                policy(
                    "activity",
                    "browser_survey.parameters.run_browser_survey_value_text",
                    str,
                )
            ) from error
        raise
