"""用户的北京时间；日历计算不依赖云端进程的系统时区。"""

from datetime import timedelta, timezone

from nyx.policy import policy

USER_TIMEZONE = timezone(
    timedelta(hours=policy("runtime", "timezone.offset_hours", int)),
    policy("runtime", "timezone.name", str),
)
