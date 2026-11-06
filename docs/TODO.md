# 统一待办清单

> 更新：2026-11-06。此文件是唯一待办入口，合并原讨论伙伴、工具升级、插件阶段 1–5 和阶段四临时清单的剩余事项。
> 已完成的代码与回归不重复列入；正式契约中的验收条目仍作为规范保留。以下事项尚未完成，不表示已批准全部实施；后续实现按项目规则单独审核。
> 工程验证改为本地执行：GitHub CI/CD 已移除，不再安排账户解锁、分支检查或自动 Release；本地测试、检查、构建及制品 smoke 保留，Railway 发布方式不变。

## 近期验证与质量门

- [ ] **本地 90% 覆盖率质量门。** 最近一次独立 pytest 覆盖率为 86.95%，历史本机合并外部 E2E 后超过 90%；现有本地命令没有合并 E2E 子进程的覆盖率。确认当前缺口并完成有效覆盖/统计接入，保持 90% 阈值，不用历史合并结果宣称当前检查已通过。依据：[本地测试与检查](../README.md#-本地测试与检查)、[测试快照](test-inventory.md)。

- [ ] **手机 QQ 内心话展示确认。** 真实手机观察“💭 内心话”独立消息及正文顺序。真实 QQ 私聊往返已由用户确认；本项只补内心话展示体验，模拟 OneBot 验收已通过。依据：[OneBot 契约](specs/21-onebot-transport.md)、[效果记录](eval/README.md)。

## 云端部署与手机跨网（Railway 路线）

- [x] **云端存储契约与 PostgreSQL 边界。** 已确定 Railway 应用 + 托管 PostgreSQL + `/app/data` 持久化卷、本地 SQLite 保留、host/character schema 隔离、迁移和备份边界。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。
- [x] **PostgreSQL 全链路集成验收。** 本机真实 PostgreSQL 已通过 host/角色 schema 导入、运行时连接、checkpoint 初始化、角色装配恢复、presence 重启重放、设备配对、领域事务回滚和 transport outbox 重放；同时修复 PostgreSQL autocommit、标量函数和字典行读取兼容性。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。
- [x] **Railway 应用制品。** Dockerfile、Compose、本地 PostgreSQL、Volume、`$PORT`、健康检查和 secret 强制注入已完成；真实 Railway 项目部署、托管 PostgreSQL/Volume 和启动恢复仍待线上环境。
- [ ] **公网域名与 TLS。** 配置真实域名、DNS、HTTPS 证书，验证手机蜂窝网络访问。本机 Caddy 内部 CA 验收已通过，公网入口尚未验收。依据：[多客户端与 HTTPS 契约](specs/22-multi-client-transport.md)。
- [x] **云端 OneBot QQ 通道。** Railway `napcat` 通过私网 `ws://napcat.railway.internal:3001` 连接 Nyx，配置的 QQ 私聊已完成登录；生产 outbox 真实发送验收仅发送一次“你好”并在 NapCat 日志确认。NapCat 未配置公网域名；临时本机 SSH 隧道和 Railway key 已撤销，临时 `WEBUI_TOKEN` 已删除。依据：[OneBot 契约](specs/21-onebot-transport.md)。
- [ ] **真实手机浏览器跨网验收。** 完成配对、REST/消息往返、SSE 断线续传、凭证撤销，并验证桌面与手机共享角色状态。依据：[设备配对契约](specs/23-device-pairing.md)。
- [x] **生产备份与隔离恢复演练。** PostgreSQL 与 `/app/data` 已导出并在隔离副本恢复，outbox 合成事件重放通过；备份位置和校验值记录在 [生产验收记录](eval/README.md#railway-生产备份与恢复演练2026-11-04)。后续备份保管、轮换和正式恢复流程由用户负责。本次未配置 Railway 平台 PITR。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。

## 公开发布

- [ ] **公开发布前决策。** 确定许可证、CSP 和安装包代码签名方案并完成相应配置；发布前使用本地版本校验与制品 smoke，当前安装包仍未签名。依据：[本地测试与检查](../README.md#-本地测试与检查)。

## 按实际需求另行排期

- [ ] **框架化后续切片。** 当前只抽出独立装配核心；传输、总线、持久化、宿主、调度尚未整体抽包。出现实际消费者后再审核范围；MCP/supervisor 也仅按需决定，不预置接口。依据：[六层设计与路线](design/plugin-harness-architecture.md)、[独立核心契约](specs/24-harness-framework.md)。
- [ ] **电脑控制输入闭环（V3 backlog）。** 当前尚未实现，实施前重新确认范围与契约。依据：[产品边界](../README.md)。
- [ ] **本地交互式 WebView 启动 smoke。** 补充真实 Windows 桌面启动验证；冻结 sidecar 的 REST/SSE、身份握手及退出回收继续使用已有本地 smoke。依据：[本地测试与检查](../README.md#-本地测试与检查)。
- [ ] **本地依赖审计基线。** 手动执行 `npm audit`、`pip-audit`、`cargo audit`，建立问题处理基线，再决定是否阻断发布。
- [ ] **前端覆盖率基线。** 先取得稳定的本地测试基线，再决定是否设置阈值；当前没有前端覆盖率硬门槛。
