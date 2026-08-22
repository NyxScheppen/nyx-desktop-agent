from nyx.activity.screen import ScreenObserver


async def test_sample_once_ok() -> None:
    async def describe(image: bytes) -> str:
        return "写代码"

    observer = ScreenObserver(lambda: b"png", describe, 60)
    assert await observer.sample_once() == "写代码"


async def test_sample_once_capture_fails() -> None:
    def capture() -> bytes:
        raise RuntimeError("无显示")

    async def describe(image: bytes) -> str:
        return "不该到这"

    observer = ScreenObserver(capture, describe, 60)
    assert await observer.sample_once() is None


async def test_sample_once_describe_fails() -> None:
    async def describe(image: bytes) -> str:
        raise RuntimeError("模型挂了")

    observer = ScreenObserver(lambda: b"png", describe, 60)
    assert await observer.sample_once() is None
