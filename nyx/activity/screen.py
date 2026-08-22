import asyncio
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

from PIL import ImageGrab

_logger = logging.getLogger(__name__)


def capture_screen() -> bytes:
    """抓全屏 → PNG bytes。纯 I/O 薄封装；失败上抛由调用方 best-effort 处理。"""
    img = ImageGrab.grab()
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ScreenObserver:
    """周期截屏 → 视觉描述 → 回调摘要。

    best-effort：单次采样失败记日志返 None，循环不中断（design §8.5 手动开启，
    主流程正确性不依赖其产出）。capture/describe 可注入（测试不碰真桌面/真模型）。
    """

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
        """一次采样：抓屏（to_thread）→ describe → 摘要；失败记日志返 None。"""
        try:
            image = await asyncio.to_thread(self._capture)
            return await self._describe(image)
        except Exception:
            _logger.exception("屏幕视觉采样失败")
            return None

    async def run(self, on_summary: Callable[[str], None]) -> None:
        """周期采样循环（永不抛；仅 CancelledError 上抛供取消）。"""
        while True:
            summary = await self.sample_once()
            if summary:
                on_summary(summary)
            await asyncio.sleep(self._interval_seconds)
