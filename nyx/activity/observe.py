def classify_presence(idle_seconds: float) -> str:
    """Classify presence solely from time since the machine's last input."""
    if idle_seconds < 30.0:
        return "online"
    if idle_seconds < 300.0:
        return "busy"
    return "away"


def build_observation_summary(
    presence: str, window_title: str, screen_summary: str
) -> str:
    """观察摘要（纯函数，屏幕视觉扩展契约见 09-activity spec）：窗口标题优先，
    视觉摘要次之逐段拼接；两者皆空则仅回 presence。"""
    if window_title:
        base = f"用户（{presence}）正在浏览 {window_title}"
    else:
        base = f"用户（{presence}）"
    if screen_summary:
        base += f"，屏幕：{screen_summary}"
    return base
