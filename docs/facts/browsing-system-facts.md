# 网页共读（共同浏览）事实摘要

> 当前后端、Windows 窗口内浏览器及开发配对入口已接线。唯一完整契约是
> `docs/specs/13-browsing-system.md`；本文不覆盖契约。

> “网页共读”是产品称呼，“共同浏览”是代码、事件和完整 spec 使用的领域名；两者指同一套功能。

## 已实现边界

- `nyx/browsing/store.py` 持有 session/page 表；schema 22 保证单活会话。
  navigation 清当前指针，capture 按导航/序列拒绝旧请求，focus 按操作 id 去重。
- 原始正文、选区和整合结果写入共用 50 MiB 检查；最多 10,000 条 page 元数据。
  只按固定规则清 failed raw，不清当前页或非终态。failed raw 保留 30 天。
- `facade.py` 先持久化 capture，再启动 companion。所有冻结入口结束该页 companion
  后 CAS 封口，后台及启动恢复补齐封口。整合 worker 使用 owner/token、300 秒 lease、
  30 秒 heartbeat 和持久化退避；父领取流程退出时回收整合子任务。
- `companions.py` 校验完整 JSON 判别联合。浏览提问复用表达 attempt/ASK 原子协议；
  联想最多三条去重记忆，snippet 使用规范化 summary-or-content 并截到 500 字符。
- `integration.py` 通过 EventBus 获取该页已提交三类展示事件；最近 100 条，按
  timestamp/id 升序返回。正文最多十个 6,000 字符 chunk，整合结果独立 checkpoint。
- MemoryFacade 固定 page id 写 BROWSING 长期记忆；记忆与稳定事件在验证 claim 的
  同一事务提交。记忆阶段重试不重复 LLM 整合。eval/embedding/图旁路不撤销核心提交。
- REST bridge 使用配对 secret/session token；UI 查询不返回正文或 lease/token。
  本机 API 已有精确 Host/Origin、CORS/PNA、路由声明媒介检查及 bridge 2 MiB 前置上限。
- 历史 metadata 默认 50、最多 100 条，page id cursor 按 captured_at/id 续页；查询仅投影
  公开字段。BrowserView 打开/显式刷新/操作完成读取第一页，加载更多续页，无周期全量轮询。
- 消费用户消息时若页面上下文已失效，runtime 发布明确 fallback SPEAK，不调用普通 reply。
- 前端三类浏览输出进入聊天且可历史回填，抑制重复 canonical ASK；REST/SSE 开发版
  共用 Vite proxy，打包构建共用固定 loopback 地址。
- `frontend/src-tauri/src/lib.rs` 固定 Tauri 2.11.5/Wry 0.55.1，Windows 直接安装 WebView2
  permission deny、导航完成/历史/崩溃事件；远程 child 不在 `webviews:["main"]` capability 内。
  首次 about:blank child 隐藏并安装 guards，再导航；profile 为 app data 下固定 `browser-profile`。
- Rust 独立预检公网 HTTPS/DNS，以进程内 token 直连 loopback bridge；DOM 先查 password
  存在性与可见 iframe，再读排除控件/可编辑/隐藏区域的正文与安全选区。
  800ms/2s/5s 采集重试，最后空正文只存元数据；focus 重试保存原 payload 并重新查隐私。
  撤销授权先修改 Rust 状态，导航 CAS 先于隐私副作用；失效 token 清页面上下文并暂停采集。
- BrowserView 常驻挂载，切视图/历史面板先 hide child，返回 show；ResizeObserver 更新矩形。
  native 激活时按物理尺寸/scale factor 把过窄主窗口扩到 960px，set-size 权限仅给 main。
  左栏聊天只在浏览视图且当前页面可用时携带 page id；浏览提问可选择 attempt_id 回复。
  历史查询/重试/删除与关闭后 profile 清理已接线，删除前确认，清理失败不报告成功。
- `python dev.py --desktop` 启动后端和 Tauri CLI，分发共享随机 256-bit secret，8000 已占用
  则拒绝启动；普通 web 模式不配对。Rust/Python 启动后移除环境 secret。
- Windows 原生 ACL/password spike 已通过，公开 Example Domain 桌面 smoke 已验证 child
  正确占位、视图切换、持久 checkpoint 和本地 SSE；使用内存 DB/mock LLM，未使用用户数据库或真实 LLM。
- Windows popup 使用 Wry deferral 下的 Create；一次许可 10 秒、窗口最长 120 秒，共享远程
  profile/opener，不指向 main。DOCUMENT WebResourceRequested deferral 在每跳连接前异步
  revoke/预检，COM 对象留在 UI 线程；实际 ResourceContext 检查避免误拦 Wry IPC。
  mock IdP 验证 cookie、postMessage、ACL、嵌套拒绝、原生关闭、302 后续预检、私网拒绝与慢预检时 UI 响应。
  认证期间撤授权/清上下文；关闭不推断成功。WebView2 WindowCloseRequested 关闭顶层窗口。
- `dev.py --build-sidecar` 冻结公开资源和默认 embedding 模型，不包含 `.env`；Tauri 生产启动
  相邻后端并私发 secret，以 stdin EOF 通知退出，35 秒超时回收自己进程；90 秒启动期限。

## 尚未实现/验收

- 非 Windows 原生权限边界尚未实现，child 创建 fail-closed；真实登录/profile 清理未人工验收。
- HTTPS server fallback 按 spec 禁用，尚未证明 DNS/连接 IP 与 TLS 主机钉扎。
- 尚未完成逐平台打包 Origin/CORS/PNA smoke test；Windows 固定 8000 被用户 Docker 占用，
  不停止用户服务；开发桌面验收不代表打包验收。
