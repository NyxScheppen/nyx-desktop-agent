# 统一待办清单

> 更新：2026-11-01。此文件是唯一待办入口，合并原讨论伙伴、工具升级、插件阶段 1–5 和阶段四临时清单的剩余事项。
> 已完成的代码与回归不重复列入；正式契约中的验收条目仍作为规范保留。以下事项尚未完成，不表示已批准全部实施；后续实现按项目规则单独审核。

## 近期验证与质量门

- [ ] **手机 QQ 内心话展示确认。** 真实手机观察“💭 内心话”独立消息及正文顺序。真实 QQ 私聊往返已由用户确认；本项只补内心话展示体验，模拟 OneBot 验收已通过。依据：[OneBot 契约](specs/21-onebot-transport.md)、[效果记录](eval/README.md)。

## 云端部署与手机跨网（Railway 路线）

- [x] **云端存储契约与 PostgreSQL 边界。** 已确定 Railway 应用 + 托管 PostgreSQL + `/app/data` 持久化卷、本地 SQLite 保留、host/character schema 隔离、迁移和备份边界。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。
- [x] **PostgreSQL 全链路集成验收。** 本机真实 PostgreSQL 已通过 host/角色 schema 导入、运行时连接、checkpoint 初始化、角色装配恢复、presence 重启重放、设备配对、领域事务回滚和 transport outbox 重放；同时修复 PostgreSQL autocommit、标量函数和字典行读取兼容性。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。
- [x] **Railway 应用制品。** Dockerfile、Compose、本地 PostgreSQL、Volume、`$PORT`、健康检查和 secret 强制注入已完成；真实 Railway 项目部署、托管 PostgreSQL/Volume 和启动恢复仍待线上环境。
- [ ] **公网域名与 TLS。** 配置真实域名、DNS、HTTPS 证书，验证手机蜂窝网络访问。本机 Caddy 内部 CA 验收已通过，公网入口尚未验收。依据：[多客户端与 HTTPS 契约](specs/22-multi-client-transport.md)。
- [ ] **真实手机浏览器跨网验收。** 完成配对、REST/消息往返、SSE 断线续传、凭证撤销，并验证桌面与手机共享角色状态。依据：[设备配对契约](specs/23-device-pairing.md)。
- [ ] **生产备份与恢复演练。** 同时备份 PostgreSQL 和 `/app/data` Volume，验证多角色、角色卡、workspace、书籍和凭证撤销状态可恢复。依据：[托管存储契约](specs/25-hosted-storage-and-postgresql.md)。

## CI/CD 与公开发布

- [ ] **GitHub 分支保护。** 在仓库设置启用 required checks，实际确认失败检查会阻止合并。依据：[CI/CD 设计](design/ci-cd.md#实施状态)。
- [ ] **首次标签线上发布预演。** 质量门通过后，使用版本一致的 `vX.Y.Z` 标签验证 draft release、MSI/NSIS、SHA256 和 release smoke；当前只完成本地构建与 smoke。依据：[CI/CD 设计](design/ci-cd.md)。
- [ ] **公开发布前决策。** 确定许可证、CSP 和安装包代码签名方案并完成相应配置，再决定是否将 draft 流程升级为自动公开。当前保持 draft/未签名边界。依据：[CI/CD 设计](design/ci-cd.md#实施状态)。

## 按实际需求另行排期

- [ ] **框架化后续切片。** 当前只抽出独立装配核心；传输、总线、持久化、宿主、调度尚未整体抽包。出现实际消费者后再审核范围；MCP/supervisor 也仅按需决定，不预置接口。依据：[六层设计与路线](design/plugin-harness-architecture.md)、[独立核心契约](specs/24-harness-framework.md)。
- [ ] **电脑控制输入闭环（V3 backlog）。** 当前尚未实现，实施前重新确认范围与契约。依据：[产品边界](../README.md)。
- [ ] **交互式 WebView 启动 smoke。** 在 Windows runner 连续稳定三次后，再决定升级为阻断门；当前冻结 sidecar smoke 已是阻断门。依据：[CI/CD 设计](design/ci-cd.md#4-release-smoke)。
- [ ] **依赖审计基线。** 按 CI 设计建立 `npm audit`、`pip-audit`、`cargo audit` 每周报告与问题处理基线，再决定是否阻断发布。依据：[CI/CD 设计](design/ci-cd.md#安全与权限)。
- [ ] **前端覆盖率基线。** 先取得稳定基线，再决定是否设置阈值；当前没有前端覆盖率硬门槛。依据：[CI/CD 设计](design/ci-cd.md#2-frontend-desktop-quality)。
