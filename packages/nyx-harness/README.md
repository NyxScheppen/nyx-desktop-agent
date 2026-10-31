# nyx-harness

Nyx 使用的进程内插件装配包，只依赖 Python 3.11+ 标准库，不导入 Nyx 领域代码。
提供受声明限制的能力上下文、稳定依赖排序、插件构建与启动前弱能力校验。

在仓库根目录执行 `uv sync --locked --extra dev`，或使用
`pip install -e ./packages/nyx-harness -e .` 同时安装两个本地包。
单独使用时只需 `pip install ./packages/nyx-harness`。

完整 API 与应用边界见仓库 `docs/specs/24-harness-framework.md`。
