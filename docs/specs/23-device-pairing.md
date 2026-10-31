# 设备配对与访问凭证（阶段四首个切片）

> 本契约覆盖阶段四的单用户设备配对。云端部署与 SQLite→Postgres 迁移仍在
> [统一待办清单](../TODO.md) 中，不能据此视为阶段四完成。

## 元信息与范围

- **前置依赖**：14-character-cards、22-multi-client-transport。
- **实现**：`nyx/app_context.py`（宿主数据库迁移）、`nyx/api/routes.py`（配对与设备 API、认证）、
  `frontend/src/stores/connectionStore.ts`、`frontend/src/api/client.ts`、登录页和设置页。
- 仍是单用户；根凭证由服务器环境变量注入。设备凭证可独立撤销，不提供账户、权限角色、
  自动配对或公网部署脚本。
- 手机复用已有浏览器前端和同源 HTTPS API；不增加 PWA 或另一套移动端应用。

## 验收标准

- 根凭证可生成十分钟有效、一次性配对码；码仅在创建响应中显示，不持久化明文。
- 配对码只可兑换一次，成功后返回一次性展示的随机设备令牌；数据库仅存 SHA-256 摘要。
- 根凭证和未撤销设备令牌均可访问现有业务 API；只有根凭证可管理配对码和设备。
- 撤销后设备令牌立即失效；清理浏览器会话凭证后不能继续调用业务 API。
- 前端连接页接受根令牌或配对码；设置页允许根凭证生成配对码、查看设备并撤销设备。
- 本地 HTTPS / SQLite 集成测试覆盖配对、业务访问、权限隔离、撤销、过期和重放。

## 配置与 API

不新增配置项。配对只在已有 `NYX_AUTH_TOKEN` 启用时提供；未配置根令牌时，阶段三本地模式
保持原有行为，设备管理端点返回 404。

- `POST /api/devices/pairing`（根凭证）：请求 `{ "name": string }`，返回
  `{ "code": string, "expires_at": number }`。名称去首尾空白后须为 1–80 个字符。
- `POST /api/devices/pair`（无需 Bearer）：请求 `{ "code": string, "name": string }`，
  返回 `{ "device_id": string, "access_token": string }`。任何未知、过期或已兑换的码统一返回
  401，不泄漏码的存在状态。
- `GET /api/devices`（根凭证）：返回设备 id、名称、创建时间及撤销时间，不返回 token/hash。
- `DELETE /api/devices/{device_id}`（根凭证）：设置 revoked_at；重复撤销幂等，未知设备 404。

配对码为密码学安全随机值，服务端只存摘要，有效期固定 600 秒，兑换时在同一事务删除码并
插入设备令牌。设备令牌由密码学安全随机源生成，原文只返回一次；数据库仅保存其 SHA-256。
过期码在扫描/兑换时删除。根凭证比对继续使用常量时间比较；设备令牌根据摘要精确查询，
并只接受 revoked_at 为 NULL 的记录。Pair API 仍受既有 Host、Origin、Content-Type 与 HTTPS
入口约束；`NYX_PUBLIC_ORIGIN` 配置时继续强制 Bearer 业务认证。

## 对象完整性

### 配对码

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 根凭证 `POST /api/devices/pairing` | 名称合法且根凭证有效 | host.db 的 pairing 表，仅摘要与期限 |
| 配对 `POST /api/devices/pair` | 摘要命中且未过期 | 同一事务删除 pairing 行并写设备凭证 |
| 过期清理 | 创建新码或兑换时发现过期行 | 删除过期 pairing 行 |

消费者为 pairing 兑换 API；无后台任务、SSE 或领域消费者。状态为 absent→active→consumed，
或 active→expired；失败事务回滚，码仍可重试，过期码不可兑换。并发重复兑换最多一个成功。
空/未知码统一 401；乱序由单个 host.db 事务串行化；重放不能再次兑换；清理由数据库删除。

### 设备凭证

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| Pair API | 有效码兑换成功 | host.db 的 device_token 表，只存 hash |
| 根凭证撤销 API | 设备存在 | 更新 revoked_at |

所有受保护的角色管理、业务、SSE 与图片 API 是消费者；设备管理 API 仅接受根凭证。
状态为 active→revoked，不可恢复；失败时事务回滚；并发撤销保持 revoked；重复请求幂等；
用户数据删除语义沿用角色存档，凭证随 host.db 删除，不级联删除角色库。

## 验证

- 后端真实 SQLite：版本迁移、摘要不含明文、一次兑换、并发重放、过期、撤销与根凭证权限。
- API：根/设备/无凭证访问、Pair Origin/Host/预检、关闭认证时端点不可用。
- 前端：连接页根令牌和配对码流程、设备列表/创建/撤销、凭证清理。
- Ruff、Pyright、pytest、前端测试与生产构建；同步测试快照和相关事实/索引。
