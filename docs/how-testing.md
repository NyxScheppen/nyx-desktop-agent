# How: 测试规范

> 测试写法细节见 `CLAUDE.md` Part 3。本文档定义策略、层级和覆盖标准。

## 测试哲学

**验证管道正确性，不验证 LLM 文本质量。**
测试确保数据走对流程、结构正确、边界不崩。不测试"Nyx 这句话说得好不好"。

## 测试层级

| 层级 | 范围 | 优先 |
|------|------|------|
| **单元** | 纯函数（计算、解析、矩阵运算） | 最高——有纯逻辑就先测 |
| **集成** | Facade 方法管道（Mock LLM） | 每个 Facade 方法 ≥1 条 |
| **E2E** | 关键用户路径（发消息→收回复） | 每条用户故事 1 条 |

## Mock 原则

- 所有 LLM 调用处可注入 mock，返回预设 fixture
- 测试不依赖真实 LLM、真实桌面、真实文件系统

## 覆盖标准

- 每个 Facade 方法 ≤ 5 个断言 | 纯函数测全 | 不追求百分比，追求"改了会不会炸"的信心

## 测试目录 & 清单

```
tests/{test_event,test_memory,test_expression,test_activity,test_desire,test_inner_life,test_eval,test_tools}/
```

每次改动测试后同步 `test-inventory.md`（当前测试套件的快照）：新增 / 删除 / 改断言对应增删改行，只记现状不记变更历史。
