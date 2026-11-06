# 使用与本地操作

所有命令从仓库根执行，明确标注的前端命令除外。代码验证见 [评测文档](eval/README.md)，生产同步见 [线上操作](deployment.md)。

## 安装与启动

需要 Python 3.11+、Node.js 18+。桌面构建另需 Tauri v2 对应的 Rust/Windows 构建环境。

```powershell
uv sync --locked --extra dev
Push-Location frontend
npm ci
Pop-Location
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 填入所选模型的密钥，默认使用 `DEEPSEEK_API_KEY`；不要把真实值放入命令或提交。检查 `config.yaml` 中的联网、视觉和 OneBot 开关，关闭本次不用的能力；启用 QQ 时需先完成下方配置。

```powershell
python dev.py
```

后端监听 8000，Vite 监听 5173，浏览器打开 `http://localhost:5173`，Ctrl+C 退出。桌面开发模式使用 `python dev.py --desktop`。开发入口监测后端和现有配置变化，前端由 Vite 热更新。

启动器会检查旧 Nyx 实例、端口与本次后端身份；未知程序占用 8000 时拒绝启动，不要用结束未知进程的方式绕过检查。

单独运行：

```powershell
uv run --no-sync python -m nyx.main
```

另一终端：

```powershell
Push-Location frontend
npm run dev
# 或 npm run tauri dev
Pop-Location
```

## 桌面构建

```powershell
uv sync --locked --extra dev --extra desktop-build
uv run --no-sync python dev.py --build-sidecar
Push-Location frontend
npm run tauri build
cargo test --manifest-path src-tauri/Cargo.toml --lib --locked
cargo check --manifest-path src-tauri/Cargo.toml --release --locked
Pop-Location
uv run --no-sync python scripts/check_release_version.py
uv run --no-sync python scripts/smoke_release.py
```

构建包含本地 embedding 模型、公开配置、prompt 和内置角色卡，不包含 `.env`；运行环境仍需提供模型密钥。开发模式通过不代表冻结制品通过，需检查实际制品的 REST/SSE、实例握手与退出回收。交互式 WebView 和各平台覆盖情况见 [待办](TODO.md)。

冻结制品冒烟验证的临时数据和日志在结束后自动清理；失败时在终端显示日志末尾。

## 配置、认证与配对

- 公开配置在 `config.yaml`，字段与支持的环境覆盖以 `nyx/config.py`、`.env.example` 为准；不要复制维护另一套字段清单。
- `NYX_CONFIG` 可选择运行配置；`NYX_DATABASE_URL` 选择 PostgreSQL，未设置时使用 SQLite。使用测试配置时同时隔离数据目录与部署环境变量。
- `NYX_AUTH_TOKEN` 启用根凭证；`NYX_PUBLIC_ORIGIN` 指定 HTTPS 入口。根凭证只经已授权的环境变量或 Secret 来源提供，不放到 URL 或前端 `VITE_*`。
- 启用认证后，连接页可用根令牌登录；在设置中用根凭证生成配对码，另一设备在连接页输入配对码和设备名。配对码一次性、十分钟有效；设备可独立撤销。
- 本机 HTTPS 使用根 `Caddyfile`。后端与 Caddy 进程需设置一致的认证环境和 `NYX_PUBLIC_ORIGIN=https://localhost:8443`，先构建 `frontend/dist`，再启动 `caddy run --config Caddyfile`。
- 本机 Caddy 使用内部 CA；需要系统信任时由用户主动执行 `caddy trust`。公开域名应使用公开证书，并去掉本机配置中的 `tls internal`；生产使用 [Railway 操作](deployment.md)。

## 角色卡

1. 复制 `nyx/cards/nyx/` 为模板，修改 `card.yaml` 的稳定小写 `id`、名称、身份、初始人格及 `canon.md`。完整字段直接参照模板和 `nyx/character_cards.py`。
2. 不使用图片时设 `assets: {}` 并移除资产目录；不设置初始长期欲望时用 `long_term_desires: []`。
3. 把文件直接放在 ZIP 根目录，命名为 `.nyxcard` 或 `.zip`，在设置的角色管理中导入。必需 `card.yaml` 和非空 `canon.md`，其余角色参考、提问方向与图片可选；压缩和解压量各限 50 MiB。
4. 使用角色选择切换；卸载会保留存档，恢复沿用成长。当前不支持 SillyTavern 卡、原地更新卡或复制存档。

本地新角色在 `data/characters/<id>/`，宿主索引在 `data/characters/host.db`；默认 Nyx 沿用原数据库与工作区。不要手动覆盖运行中的角色文件。

## OneBot 私聊

1. 安装并登录 NapCat/LLOneBot，启用 OneBot v11 正向 WebSocket 与消息数组格式；本机示例监听 `127.0.0.1:3001`，设置 `access_token`。
2. 在本机 `.env` 中设置相同的 `ONEBOT_ACCESS_TOKEN`。
3. 在 `config.yaml` 的 `onebot` 中启用连接，填写机器人 `self_id`、本人 `owner_id`、已注册的 `character_id` 与服务地址；不要提交私人配置或凭证。
4. 重启 Nyx，从本人账号私聊机器人，核对回复和“💭 内心话”的展示。断开再恢复服务，检查未确认消息续发；验收仅在用户授权的真实外发范围内进行。

只接指定本人私聊；QQ 与桌面当前角色选择独立。QQ 不可用时出向保留待续发，极端崩溃窗口仍可能重复一次。不要建立竞争的第二条生产会话。

## 本地 PostgreSQL

先准备 `.env`；Compose 要求根凭证，模型密钥按需填写。下例只在尚未设置根凭证的本机模板中写入随机值，不输出它：

```powershell
python -c "from pathlib import Path; import secrets; p=Path('.env'); s=p.read_text(); p.write_text(s.replace('NYX_AUTH_TOKEN=\n', 'NYX_AUTH_TOKEN=' + secrets.token_urlsafe(32) + '\n', 1))"
docker compose up --build
```

应用在 `http://localhost:8000`。`docker compose down` 停止服务并保留数据；只有明确要清空本地数据库卷时才使用 `docker compose down -v`。

## SQLite 迁移到 PostgreSQL

1. 停止源端写入并备份数据库、角色卡与工作区。将 `$sourceDataRoot`、`$targetDataRoot` 设为已核对的源和目标绝对路径。准备独立的空目标，通过 `NYX_DATABASE_URL` 或 `--database-url-env` 指定的环境变量提供连接 URL，不将 URL 写在命令参数里。
2. 先只读预检源数据根：

```powershell
uv run --no-sync python -m scripts.migrate_sqlite_to_postgres --source $sourceDataRoot
```

3. 确认预检支持源版本、目标 schema 为空、目标文件目录正确后，再显式导入：

```powershell
uv run --no-sync python -m scripts.migrate_sqlite_to_postgres --source $sourceDataRoot --import --target-data-root $targetDataRoot
```

4. 检查报告中的表行数、主键、事件顺序、版本、角色路径、凭证摘要及文件校验；全部成功后才切换运行时。导入器不覆盖、合并已有目标，不删除源文件；非空 SQLite checkpoint 暂不导入。
5. 失败时检查本次目标清理结果，保留源端与备份；回退旧 SQLite 只是回到迁移前快照，不会自动接回 PostgreSQL 上的新增写入。恢复须匹配数据库和文件，生产操作另按 [线上文档](deployment.md)。

支持的版本与具体校验逻辑查 `scripts/migrate_sqlite_to_postgres.py`，不在文档维护版本清单。

## 网页问卷

Windows 优先使用已安装的 Edge；其他环境按需执行 `uv run playwright install chromium`。在设置中开启联网探索，在活动页提交公开问卷 URL，或在聊天中委派填写；最终结果在活动页查看。登录和验证码页面不自动填写。
