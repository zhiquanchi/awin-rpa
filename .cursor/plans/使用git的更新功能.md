发现了关键信息。dulwich 确实提供了命令行工具 `dulwich`，从文档的 changelog 可以看到有大量 CLI 命令如 `dulwich clone`、`dulwich pull`、`dulwich diff` 等。

让我确认一下：

**dulwich 自带 CLI 命令行工具。** 从 [dulwich 文档](https://dulwich.io/docs) 的 changelog 中可以看到：

- `dulwich clone` - 克隆仓库
- `dulwich pull` - 拉取更新
- `dulwich status` - 查看状态
- `dulwich diff` - 查看差异
- 等等，几乎所有常见的 git 命令都有

所以你的用户完全可以这样操作：

### 首次获取代码

由于用户已经安装了 `uv`，而 dulwich 是项目依赖，用户可以直接用 `uvx` 来运行 dulwich 命令，**不需要额外安装任何东西**：

```powershell
uvx --from dulwich dulwich clone https://github.com/zhiquanchi/awin-rpa.git
```

或者你帮用户在命令行跑一下就行。

### 后续更新

用户在项目目录里执行：

```powershell
uvx --from dulwich dulwich pull
```

或者程序内部用 `porcelain.pull()` 自动完成。

### 结论

- 用户 **不需要安装系统 Git**
- 用户 **不需要额外安装 dulwich**（项目依赖已包含）
- 通过 `uvx` 就可以在命令行使用 `dulwich clone` / `dulwich pull`
- 程序内部用 `porcelain.pull()` 实现自动更新

这比方案 A（逐文件下载覆盖）简单得多。要切换到这个方案的话，切 Agent 模式我来重写 `Updater` 类。