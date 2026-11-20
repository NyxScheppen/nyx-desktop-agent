
from nyx.policy import policy


def classify_presence(idle_seconds: float) -> str:
    """Classify presence solely from time since the machine's last input."""
    if idle_seconds < policy(
        "activity", "observe.parameters.online_idle_seconds", float
    ):
        return "online"
    if idle_seconds < policy("activity", "observe.parameters.busy_idle_seconds", float):
        return "busy"
    return "away"


def build_observation_summary(
    presence: str, window_title: str, screen_summary: str
) -> str:
    """观察摘要（纯函数）：窗口标题优先，
    视觉摘要次之逐段拼接；两者皆空则仅回 presence。"""
    if window_title:
        base = policy(
            "activity", "observe.templates.build_observation_summary_text", str
        ).format(p0=presence, p1=window_title)
    else:
        base = policy(
            "activity", "observe.templates.build_observation_summary_text_2", str
        ).format(p0=presence)
    if screen_summary:
        base += policy(
            "activity", "observe.templates.build_observation_summary_text_3", str
        ).format(p0=screen_summary)
    return base
