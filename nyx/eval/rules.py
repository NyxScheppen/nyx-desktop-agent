"""eval 规则评分：纯函数，无 IO、无 LLM、无 DB。"""

# 每命中一个表达信号扣 0.5；仅供行为反思核实。
_OOC_STEP = 0.5

# OOC 关键词（MVP 初始值，可推翻，随 canon.md 校准）：
# 不以特定角色的称呼抵消信号。
_BLACKLIST: frozenset[str] = frozenset(
    {
        "作为一个AI",
        "我是一个人工智能",
        "作为AI助手",
        "作为语言模型",
        "无法回答",
        "让我来帮你",
        "your request",
        "As an AI",
    }
)


def ooc_score(content: str) -> float:
    """表达信号评分（保留字段名 ooc），范围 [0,1]。

    1.0 表示未命中，越低表示命中信号越多。
    是否偏离角色须由行为反思结合执行时的人设参照核实。
    """
    black = sum(1 for w in _BLACKLIST if w in content)
    return max(0.0, min(1.0, 1.0 - black * _OOC_STEP))
