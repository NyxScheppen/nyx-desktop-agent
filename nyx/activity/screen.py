from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

from PIL import Image, ImageGrab, UnidentifiedImageError

from nyx.types import ValidatedFrame, WindowTarget

_MAX_FRAME_BYTES = 4 * 1024 * 1024
_logger = logging.getLogger(__name__)


def capture_screen() -> bytes:
    """抓全屏 → PNG bytes。旧观察功能保留；陪玩不会调用这个 fallback。"""
    image = ImageGrab.grab()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ScreenObserver:
    """周期截屏 → 视觉描述 → 回调摘要。"""

    def __init__(
        self,
        capture: Callable[[], bytes],
        describe: Callable[[bytes], Awaitable[str]],
        interval_seconds: int,
    ) -> None:
        self._capture = capture
        self._describe = describe
        self._interval_seconds = interval_seconds

    async def sample_once(self) -> str | None:
        """一次采样失败只影响旁路屏幕摘要。"""
        try:
            image = await asyncio.to_thread(self._capture)
            return await self._describe(image)
        except Exception:
            _logger.exception("屏幕视觉采样失败")
            return None

    async def run(self, on_summary: Callable[[str], None]) -> None:
        """周期采样，取消由调用方处理。"""
        while True:
            summary = await self.sample_once()
            if summary:
                on_summary(summary)
            await asyncio.sleep(self._interval_seconds)


def validate_bridge_frame(
    target: WindowTarget,
    image_bytes: bytes,
    capture_id: str,
    expected_revision: int,
) -> ValidatedFrame:
    """Validate one transient PNG frame received from the native bridge."""
    if not capture_id.strip():
        raise ValueError("capture_id 不能为空")
    if expected_revision < 0:
        raise ValueError("expected_revision 非法")
    if not image_bytes or len(image_bytes) > _MAX_FRAME_BYTES:
        raise ValueError("capture_too_large")
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format != "PNG":
                raise ValueError("capture_invalid")
            width, height = image.size
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("capture_invalid") from error
    bounds = target.client_bounds_physical
    if width <= 0 or height <= 0 or width > max(bounds.right - bounds.left, 1) * 2:
        raise ValueError("capture_invalid")
    if height > max(bounds.bottom - bounds.top, 1) * 2:
        raise ValueError("capture_invalid")
    return ValidatedFrame(
        capture_id=capture_id,
        image_bytes=image_bytes,
        width=width,
        height=height,
        window_id=target.window_id,
        expected_revision=expected_revision,
    )
