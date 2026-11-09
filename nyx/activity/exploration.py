"""Activity entry point for the shared research engine."""
from typing import Any

from nyx.research.engine import ResearchEngine, should_explore
from nyx.types import Activity


class Exploration(ResearchEngine):
    """Compatibility adapter; research execution lives in nyx.research."""

    async def run(self, activity: Activity) -> dict[str, Any]:
        result = await super().run(activity)
        parent = activity.progress.get("parent_long_term_id")
        strong = result.get("strong_new_topics")
        if self._desire and isinstance(parent, str) and strong:
            await self._desire.add_long_term_subtopics(parent, strong)
        return result


__all__ = ["Exploration", "should_explore"]
