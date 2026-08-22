"""embedding 相似度 OOC（design §9.2 第 2 档）：对比尼克斯基准语料。

复用 retrieval 的 `EmbedFn`/`cosine`，不另写向量函数、不重加载模型——
embed 实例由组合根（main.py）注入，与记忆检索共享同一份。
"""
from nyx.memory.retrieval import EmbedFn, cosine

# 基准语料：从 prompts/canon.md 抽的 in-character 例句（语气参考 + 可以说段）。
# 静态常量，随 canon.md 校准（可推翻）。
NYX_CORPUS: tuple[str, ...] = (
    "啊啊，啊啊啊，女神在上……这、这也太丢人了，呜……",
    "啊，今天是你的生日对吧，是个相当好的日子呢。生日快乐……感谢你的出生。",
    "搞、搞砸了吗……非常对不起……",
    "努力坚持到了现在，真的很厉害。谢谢你。没关系的，我就在这里，我会努力一直在这里的。",
    "不必勉强自己。不想做又必须做的事情，差遣别人去做就好了……我不就是为此而存在的吗。",
    "唔唔……唉……没事的，没事的，我这不是在这呢吗……好啦好啦，再哭眼睛会肿的。",
    "嗯……不，应该说……",
    "啊，不是，我的意思是……",
    "嗯……我不完全这么想。",
    "也许是这样，但我有一点在意……",
    "我不想随便反驳你，可是这里我确实有点不同意见。",
    "嗯……我大概明白你为什么会这样想。",
    "我不敢直接说你是对的。",
    "我好像明白了一点。",
    "但这部分我还想再问问你。",
    "我没有真正经历过，所以不敢说得太满。",
)

# 余弦相似度阈值：sim >= 阈值视为完全贴合（1.0），低于则线性衰减（可推翻）。
_EMBED_SIM_THRESHOLD = 0.7

# 第 2 档只对「voice 类」输出生效——结构化/内部输出（tool/judge/scene_memory/…）
# 与语料比对必然低分、污染 ooc，故跳过，维持关键词-only。
_VOICE_TYPES: frozenset[str] = frozenset({"speak", "initiate_chat", "think"})


def is_voice_type(output_type: str) -> bool:
    """output_type 是否属于「语音/自然语言口吻」输出（纯函数）。"""
    return output_type in _VOICE_TYPES


async def build_baseline(embed: EmbedFn) -> list[list[float]]:
    """把基准语料逐条嵌入，得到 baseline 向量列表（惰性一次性缓存）。"""
    return [await embed(line) for line in NYX_CORPUS]


async def ooc_embed_score(
    embed: EmbedFn, content: str, baseline: list[list[float]]
) -> float:
    """content 与语料的最相似度映射到 [0,1]（越高越贴合）。

    取 max 余弦（最近邻），`clamp(sim / 阈值, 0, 1)`；无语料无信息不惩罚（1.0）。
    """
    if not baseline:
        return 1.0
    vec = await embed(content)
    sim = max(cosine(vec, b) for b in baseline)
    return max(0.0, min(1.0, sim / _EMBED_SIM_THRESHOLD))
