# 欲望系统事实摘要

> 本文件只记录当前实现的快速导航事实，不定义契约。欲望系统唯一完整契约是
> `docs/specs/11-desire.md`；本摘要与完整 spec 不一致时按完整 spec 执行并修正摘要。
> 修改 `nyx/desire/`、欲望消费、活动结算或欲望相关事件前，先读完整 spec。

## 源码入口

- `nyx/desire/store.py`：三张欲望表的 CRUD、原子压力/状态操作、生成尝试和容量裁剪。
- `nyx/desire/lifecycle.py`：压力、衰减、达峰生成、满足、淘汰和状态流转。
- `nyx/desire/facade.py`：事件入口、读快照、活动 claim 和长期欲望新增。
- `nyx/desire/value.py`：纯数值函数；不在 lifecycle 中重复实现。
- `nyx/bootstrap.py`：四类压力值和三条初始长期欲望的幂等 seed。

## 数据事实

- `short_term_desire` 保存短期欲望和 `PENDING / ACTIVE / SUPPRESSED / SATISFIED / EXPIRED` 状态。
- `desire_value` 每种 `DesireType` 一行，包含压力、表达权重、抑制阈值和 `updated_at`。
- `long_term_desire` 保存长期欲望；`name_normalized` 由规范化名称生成并有唯一索引。
- `desire_generation_attempt` 保存已解析但正式写入尚未完成的 LLM 结果，提交成功后删除。

## 当前状态流

```text
PENDING --claim_for_activity--> ACTIVE
ACTIVE --satisfy--> SATISFIED
ACTIVE --retry/partial--> PENDING
ACTIVE --fail/interrupt--> SUPPRESSED
SUPPRESSED --next evaluable tick--> PENDING
PENDING --retry limit--> EXPIRED
```

`get_pending()` 只返回 `PENDING`，活动启动不可依赖“先读到再标记”的两步窗口。

## 产生与消费

- `OBSERVATION_STATE` 原子增加互动欲压力。
- `ACTIVITY_END` 按 payload 的 `desire_id`/`goal_met` 结算，并对读书/自由探索增加创造欲压力。
- `DESIRE_EVAL` 先在短事务内结算衰减和周期压力，再在事务外调用 LLM。
- 同一进程的评估调用由 lifecycle 锁串行；最终压力重置使用 `updated_at` 条件，保护评估期间的新压力。
- 解析成功的 LLM JSON 先进入 generation attempt；正式状态、容量裁剪和 `DESIRE_GENERATED` 在一个事务中提交。
- 短期容量溢出时，按类型表达权重降序、创建时间升序保留，低表达权重待消费欲望被裁剪。
- 长期欲望 embedding 是严格前置；embedding 失败不插入，不降级为仅名称去重。

## 跨模块事务

- 活动 starter 在同一数据库事务中 claim 欲望并插入活动；任一步失败都会回滚。
- 活动完成事件由活动事务产生；欲望消费者用 `(event_id, consumer_id)` effect marker 防重。
- 欲望满足/淘汰的欲望状态、值强化/回灌、长期进度和终局事件在同一本地事务中提交。
- 事务提交后的 announce/wake 失败只影响唤醒，不得反向修改已提交的业务事实；总线恢复扫描负责补投递。
