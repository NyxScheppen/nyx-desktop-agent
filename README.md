# Nyx Agent

持续运行的桌面 AI 同伴：记住共同经历，形成欲望，安排活动，在合适的时候主动表达。支持角色卡、共读、多角色，以及桌面、浏览器和 QQ 接入。

后端：Python 3.11+ / FastAPI / LangGraph / SQLite、PostgreSQL。前端：React 18 / TypeScript / Zustand / Vite / Tauri v2。

## 开始使用

按 [本地操作](docs/operations.md) 安装依赖并配置 `.env`，然后在仓库根运行：

```powershell
python dev.py
```

浏览器打开 `http://localhost:5173`；桌面开发使用 `python dev.py --desktop`。

## 文档

| 内容 | 入口 |
|---|---|
| 完整导航与各系统基本事实 | [文档入口](docs/README.md) |
| 安装、启动、构建、配置、角色卡、QQ、迁移 | [本地操作](docs/operations.md) |
| Railway 发布、生产验收、回退与备份 | [线上操作](docs/deployment.md) |
| 开发方式与验证原则 | [开发约定](docs/development.md) |
| 路径验证与实际行为评测 | [评测文档](docs/eval/README.md) |
| 项目基本设计思路 | [设计思路](docs/design/design.md)（不作为系统事实参考） |
| Nyx 静态设定与未完成事项 | [角色设定](docs/canon.md)、[待办](docs/TODO.md) |

具体实现查代码；基本事实或操作方式变化时才更新对应文档。测试以路径分析和实际行为评测为主，单元测试非必要不增加。

## 使用边界

单用户运行，可选择本地或自己的 Railway 项目托管。公网使用 HTTPS、Bearer 与设备配对；联网探索、屏幕观察和 QQ 按配置启用。通用电脑输入控制闭环尚未实现。密钥、数据库与用户运行数据不提交仓库。
