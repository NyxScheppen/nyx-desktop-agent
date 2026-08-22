def classify_presence(
    keyboard_active: bool, mouse_active: bool, window_title: str
) -> str:
    """观察用户：键盘/鼠标活跃度 + 前台窗口标题 → 在线/离开/忙碌
    （纯函数，design §8.5）。

    MVP 简化规则：键盘或鼠标活跃 → "online"；否则窗口标题非空 → "busy"；
    否则 "away"。运行时调用方是前端 ingress（Tauri 壳采集后判定的单一事实来源）；
    本 spec 保留为可展示/可测的规则定义。
    """
    if keyboard_active or mouse_active:
        return "online"
    if window_title:
        return "busy"
    return "away"


def build_observation_summary(
    presence: str, window_title: str, screen_summary: str
) -> str:
    """观察摘要（纯函数，design §8.5 屏幕视觉扩展）：窗口标题优先，
    视觉摘要次之逐段拼接；两者皆空则仅回 presence。"""
    if window_title:
        base = f"用户（{presence}）正在浏览 {window_title}"
    else:
        base = f"用户（{presence}）"
    if screen_summary:
        base += f"，屏幕：{screen_summary}"
    return base
