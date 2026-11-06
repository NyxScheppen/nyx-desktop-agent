"""用户的北京时间；日历计算不依赖云端进程的系统时区。"""

from datetime import timedelta, timezone

USER_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")
