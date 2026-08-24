"""eval 规则评分：纯函数，无 IO、无 LLM、无 DB。"""

# 人设贴合度步长：每命中一个黑名单扣 0.5、白名单加 0.5（可推翻）
_OOC_STEP = 0.5

# OOC 关键词（MVP 初始值，可推翻，随 canon.md 校准）：
# 黑名单 = 崩人设的现代/AI 腔；白名单 = Nyx 语癖。
_BLACKLIST: frozenset[str] = frozenset({
    "作为一个AI", "我是一个人工智能", "作为AI助手", "作为语言模型",
    "无法回答", "让我来帮你", "your request", "As an AI",
})
_WHITELIST: frozenset[str] = frozenset({
    "小狐狸", "夏本",
})


def ooc_score(content: str) -> float:
    """人设贴合度（字段名 ooc 保留，01-types 锁定）：白名单命中加分、
    黑名单命中扣分，[0,1]。

    1.0 = 完全贴合（无 OOC），0.0 = 严重 OOC。越高越好。
    无命中默认满分（大部分正常输出无黑名单词 = 贴合）。
    """
    black = sum(1 for w in _BLACKLIST if w in content)
    white = sum(1 for w in _WHITELIST if w in content)
    return max(0.0, min(1.0, 1.0 - (black - white) * _OOC_STEP))
