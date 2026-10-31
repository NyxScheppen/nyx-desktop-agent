# OneBot 私聊传输（阶段 2）

> 契约定义签名、语义和决策，源码是实现唯一事实来源。方案于2026-10-30获批。

## 元信息与范围

依赖04总线、14角色宿主、20插件装配。实现位于 `nyx/transports/{__init__,onebot,outbox}.py`。
用户通过QQ私聊同一个角色，共享桌面记忆/欲望/状态。OneBot v11正向WebSocket连接本地
NapCat/LLOneBot，不引入NoneBot2、群聊、媒体或QQ切角色命令。

## 配置与接口

`OneBotConfig`：enabled=False、ws_url="ws://127.0.0.1:3001"、
access_token_env="ONEBOT_ACCESS_TOKEN"、self_id=0、owner_id=0、character_id="nyx"。
启用时两QQ号必须是不同正整数；URL仅本机loopback的ws/wss，不允许凭证/query/fragment。
token仅从环境变量读取，缺失拒绝启动，不记录token。

`TransportAdapter` Protocol：name:str；异步方法
`on_external(payload: dict[str, Any]) -> None`、`emit(event: Event) -> None`、
`run() -> None`、`quiesce() -> None`。唯一实现 `OneBotAdapter(app)`。
`TransportOutbox(database, transport)` 提供异步 initialize、scan、claim、sent、retry、release；
claim返回Event或None，sent/retry按event_id确认。无Manager或第二套总线。
quiesce关闭入向准入并请求取消run；runtime持有并等待实际run任务，保持停止等待有界。
OneBotError是网络边界及协议拒绝的本地异常；其消息不携带远端正文或凭证，用于与DB故障区分。

## 入向、角色与输出范围

- 仅接受post_type=message、message_type=private且self_id/owner_id匹配的纯文本；支持text数组
  和CQ转义字符串，含非文本段、空文本、机器人自己、群及其他用户忽略。
- USER_MESSAGE/EXTERNAL的id与correlation_id为 `onebot:<self_id>:<message_id>`，允许负message_id；
  timestamp为OneBot time，content包含message、transport、self_id、user_id、message_id。
  调用原bus.publish持久受理及去重；复用所有原消息消费者、历史与SSE。
- QQ角色绑定独立于桌面选择。RUNNING且被桌面或启用的QQ绑定时允许主动表达准入；原presence、
  精力、冷却条件保留。连接及私聊不写成桌面在线观察。
- 绑定角色所有非空THINK/SPEAK/ASK的content文本同步QQ，包括桌面产生的回复。
  THINK独立成一条消息，发送时在原文前添加`💭 内心话\n`；SPEAK/ASK保持原文。
  不外发MUTTER、INITIATE_CHAT等其他类型，不改变原表达事件或增加模型调用。
  沿用日志提交顺序，正常回复批次中先内心话再正文；多轮内容按原事件顺序逐条发送。
  原事件和outbox不写展示前缀，不重置游标、不回补已扫描的历史内心话。

## 持久队列与事务

角色数据库schema51新增：

- transport_checkpoint(transport PRIMARY KEY, scan_rowid, sent_rowid)。
- transport_outbox(transport,event_id,event_rowid,status,attempts,available_at,lease_until,last_error)，
  主键(transport,event_id)，event_id外键指向event_log、删除级联。

transport身份为 `onebot:<self_id>:<owner_id>`，角色由独立DB隔离；改QQ号建立新身份，旧队列不发给
新接收人；改URL/token不重置游标。角色装配在start hooks前initialize：首次以event_log最大rowid
建立起点，恢复不覆盖。日志无运行时删除/压缩，rowid用作提交扫描顺序，不按外部timestamp排序。

scan每批最多100条，入队和scan_rowid推进同事务，无输出也推进。claim按event_rowid FIFO，
仅最早未发送项可领取；processing lease60秒、attempts加一，前项等待重试/lease时不越过。
崩溃后过期processing重新领取；sent仅在匹配echo、status=ok且retcode=0后，同事务标记sent并
推进sent_rowid；retry保留pending，退避1/2/4/8/16/30秒，上限30秒。release在worker退出后
释放本实例processing。网络发送在事务外，使用text数组防CQ注入。

API回执等15秒、连接握手5秒、关闭等待1秒；普通网络错误按1至30秒重连，不导致角色FAILED；
DB/编程错误传播。每次连接先get_login_info核对self_id，一个receiver分流入向与echo。
last_error/日志不保存正文或凭证。至少一次投递：对端已发送而本地未确认时可能重复，
OneBot无幂等键，不保证exactly-once。

## 生命周期

适配器随绑定角色运行。STOPPING/宿主关闭先禁止新入向、停止并等待网络任务，然后原tick、
memory、handler drain与DB关闭。writer未退出则保留STOPPING和DB。已受理工作后续生成的输出
留在日志供恢复扫描，不因QQ离线阻止卸载。关闭配置保留队列，再启用续发；归档保留数据。

## 对象完整性

### 入向消息

入口：WebSocket私聊/对端重放；写入event_log和原event_delivery。
消费者：所有原USER_MESSAGE表达/记忆消费者、桌面历史/SSE。

| 状态 | 条件 | 下一状态/副作用 |
|---|---|---|
| 未受理 | publish提交 | durable，原消费者执行 |
| durable | 相同ID重放 | 不新增事件/回复；内容冲突沿用总线拒绝语义 |

| Bad case | 处理 |
|---|---|
| 空 | 忽略空文本 |
| 失败 | publish错误传播；OneBot不承诺离线入向补投 |
| 部分完成 | durable之后回复恢复沿用原总线 |
| 乱序 | 按接收受理，不根据外部time改写旧事件 |
| 重放 | 稳定根ID去重 |
| 删除 | 无新增删除入口，随角色DB处理 |

### 出向项与游标

入口：装配initialize、原所有THINK/SPEAK/ASK写入口的日志扫描、回执sent/retry、过期lease和正常停止。
消费者：扫描器、FIFO发送worker、恢复任务。首次初始化无历史投递。
THINK新增QQ展示消费者，桌面SSE/历史等旧消费者不变；持久状态和迁移不变。

| 状态 | 条件 | 下一状态/副作用 |
|---|---|---|
| 无checkpoint | 首次启用 | 日志末尾初始化 |
| 未扫描 | scan提交 | pending与scan_rowid原子推进 |
| pending/过期processing | FIFO可领取 | processing+lease+attempts |
| processing | 成功回执 | sent与sent_rowid原子推进 |
| processing | 失败/正常停止 | pending；失败保留退避 |
| sent | 重启/扫描重放 | 保留sent，不重发 |

| Bad case | 处理 |
|---|---|
| 空 | 无项不发送，空输出（含THINK）跳过，不单独发送前缀 |
| 失败 | 事务回滚不推进位置；网络失败保留队列退避 |
| 部分完成 | 扫描/确认各自原子；对端成功、本地未确认可能重复 |
| 乱序 | 提交rowid FIFO，无关echo不确认，前项失败不越过 |
| 重放 | 唯一键、sent、持久游标和lease保持续发；内心话不重复添加前缀或回补已扫描历史 |
| 删除 | 归档保留；event_log删除时outbox级联；不新增单项删除API |

## 验收标准

- 配置/迁移、文本过滤、重复入向、实库事务/FIFO/lease/磁盘恢复。
- 本地模拟OneBot WS+脚本化LLM完整入向→脑→出向；echo、失败重连、停止恢复。
- Ruff、Pyright、全量pytest；真实NapCat/QQ登录往返单独记录，模拟不能替代真实验收。

## 本机配置步骤

1. 在本机安装并登录NapCat/LLOneBot机器人账号，启用OneBot v11正向WebSocket服务，
   监听127.0.0.1:3001，设置access_token；使用消息数组格式。Nyx无需新增HTTP监听端口。
2. 在本地`.env`设置`ONEBOT_ACCESS_TOKEN`，值与OneBot服务一致；不要提交或贴到聊天中。
3. 修改config.yaml的onebot段：enabled=true，self_id填写机器人QQ、owner_id填写你的QQ，
   character_id默认nyx；地址/端口与本机服务对应。其他角色需先注册，随后按绑定角色生命周期运行。
4. 重启Nyx，从owner账号私聊机器人，核对桌面历史与QQ均收到回复。再断开并恢复OneBot服务，
   确认未确认消息继续投递、已确认消息不重发。QQ不可用时桌面仍可使用，输出保留待续发。

用户已确认阶段2真实QQ往返通过（2026-10-30）；本次THINK展示扩展由模拟WS和真实宿主回归验证，
手机QQ显示仍待用户体验确认。仓库保留默认关闭及空凭证模板。
