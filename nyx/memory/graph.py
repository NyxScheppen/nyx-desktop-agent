import networkx as nx

from nyx.types import MemoryEdge


class MemoryGraph:
    """联想图：memory 为节点、memory_edge 为加权边。

    构建自 edges，沿边扩散找相关记忆。
    """

    def __init__(self, edges: list[MemoryEdge]) -> None:
        self._g: nx.Graph[str] = nx.Graph()
        for e in edges:
            self._g.add_node(e.from_id)
            self._g.add_node(e.to_id)
            self._g.add_edge(e.from_id, e.to_id, weight=e.weight)

    def neighbors(self, seeds: list[str], depth: int = 1) -> list[str]:
        """从 seeds 沿边扩散 depth 跳，返回可达节点 id（不含 seeds，按发现序）。

        不在图中的 seed（无任何边）直接跳过：nx.Graph.neighbors 对不存在的
        节点抛 NetworkXError，而非返回空。keyword/vector 命中的记忆常无边，
        必须过滤，否则 search() 崩。
        """
        frontier: list[str] = [s for s in seeds if self._g.has_node(s)]
        seen: set[str] = set(frontier)
        result: list[str] = []
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                for n in self._g.neighbors(node):
                    if n not in seen:
                        seen.add(n)
                        nxt.append(n)
                        result.append(n)
            frontier = nxt
        return result
