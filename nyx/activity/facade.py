from nyx.types import Activity


class ActivityFacade:
    """14-activity 占位 stub：12-inner-life 只依赖 get_current。

    真实实现归 13/14-activity（on_tick / select_activity / get_current 等），
    届时替换本文件。当前 get_current 恒返回 None（无进行中活动）。
    """

    async def get_current(self) -> Activity | None:
        return None
