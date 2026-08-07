# agent-cli-governor

[English](./README.md) | [简体中文](./README.zh-CN.md)

审计并治理本机安装的 Agent CLI 与相邻运行时工具。

`agent-cli-governor` 是一个面向本地运维的小型仓库，解决三个相关问题：

- 识别一台机器上已安装的 Agent CLI
- 检查当前安装渠道是否仍符合厂商建议
- 生成保守的升级和迁移建议

## 提供内容

- `agent_cli_audit.py`
  检查已安装工具、识别安装渠道、对比当前与最新版本、概括发布风险；当安装方式偏离厂商建议时给出迁移建议。
- `agent_cli_upgrade.py`
  基于审计结果生成保守的升级计划，并可选择执行已经批准的升级。
- `agent_cli_catalog.json`
  政策目录，定义受跟踪的工具、安装渠道及特定来源的最新版本查询方式。
- `gui.py`
  轻量 NiceGUI 原型；它可视化既有 CLI 和 JSON 输出，不替代 CLI-first 核心。

## 覆盖范围

主要关注以下 `agent-cli` 工具：

- OpenAI Codex CLI
- Claude Code
- Gemini CLI
- Kiro CLI
- Devin CLI
- Hermes CLI
- Google Antigravity CLI (`agy`)
- Copilot CLI
- OpenCode
- Multica
- Amp
- Droid
- Kilo Code
- Cline

目录也可保留少量与这套本地治理工作流相关的运行时工具（`tooling-runtime`），例如 `uv`。

## 要求

- macOS，或其他能在 `PATH` 中找到受跟踪 CLI 的环境
- Python 3
- 在目标机器上可选但通常预期存在的工具：
  - `brew`
  - `npm`
  - 用于查询最新版本和发布说明的网络访问

CLI 工具当前不需要安装任何 Python 包。

## 用法

### 审计

```bash
python3 agent_cli_audit.py
python3 agent_cli_audit.py --offline
python3 agent_cli_audit.py --json
python3 agent_cli_audit.py --all
python3 agent_cli_audit.py --only-outdated
python3 agent_cli_audit.py --only-nonstandard
python3 agent_cli_audit.py --only-class agent-cli
python3 agent_cli_audit.py --only-class tooling-runtime
python3 agent_cli_audit.py --with-release-notes
```

### 生成升级计划

```bash
python3 agent_cli_upgrade.py
python3 agent_cli_upgrade.py --offline
python3 agent_cli_upgrade.py --channel recommended
python3 agent_cli_upgrade.py --channel supported
python3 agent_cli_upgrade.py --tool codex --tool gemini
python3 agent_cli_upgrade.py --tool uv --apply
```

### GUI 原型

```bash
python3 -m pip install -r requirements-gui.txt
python3 gui.py
```

本地开发 GUI 时启用 NiceGUI 热重载：

```bash
python3 gui.py --reload
```

GUI 入口已针对 NiceGUI 多进程热重载处理；下游 fork 应使用以上命令，而不要把 `ui.run()` 放在普通的 `if __name__ == "__main__":` 保护中。

GUI 有意保持为既有 CLI 工具上的薄壳：

- `Overview` 说明升级模型并展示静态示例数据
- `Console` 运行本地审计并生成 dry-run 升级计划，绝不执行升级
- `Generate Upgrade Plan` 会在可能时复用最近一次匹配的审计结果，避免第二次完整网络审计
- 选定 CLI 的 `Details` 面板可按需加载发布说明，避免 changelog 获取阻塞主审计
- `Plan scope` 仅控制生成的升级计划；`Installation status` 和 `Only outdated` 是结果表过滤器，统计卡片仍以未过滤审计结果为基线
- 在线审计最多并发探测四个 CLI；单次 HTTP 与包管理器查询均受限，失败会显示在 `Audit Warnings` 中
- 本地版本和包管理器探测在隔离进程组中运行，超时的启动器不会遗留本机子进程
- Python HTTP 查询采用默认 `urllib` 代理解析：`HTTP_PROXY`/`HTTPS_PROXY` 优先；未设置时使用 macOS 系统代理，并将解析后的代理传递给 `npm` 和 Homebrew 查询
- 长时间运行的子进程有明确超时，并会被终止而非留在后台
- 第一版不执行真实升级

运行 `python3 gui.py` 即可。如果 `8080` 已被占用，可选择其他端口，例如 `python3 gui.py --port 8081`。

### GUI 截图：总览

![agent-cli-governor GUI overview screenshot](./docs/assets/gui-overview.png)

### GUI 截图：控制台

![agent-cli-governor GUI console screenshot](./docs/assets/gui-console.png)

## 输出模型

审计输出会区分：

- `tooling_class`
  `agent-cli` 与 `tooling-runtime`
- `normalized_channel`
  例如 `script`、`npm`、`brew-core`、`brew-cask`、`desktop-install`
- `binary_container`
  实际二进制文件的封装形式，目前为 `standalone` 或 `app-bundle`。它与安装渠道不同：官方脚本可在 `~/.local/bin` 安装一个 shim，而其最终指向 app bundle 中的可执行文件。
- `channel_status`
  安装渠道与厂商建议的关系：`recommended`（厂商首选）、`supported`（仍受支持但非首选）或 `nonstandard`（不在支持范围内）
- `update_command`
  为 `--apply` 选出的唯一可执行安全默认命令
- `upgrade_guidance`
  结构化的主操作标题、理由、对安装渠道的影响，以及可选的仅供参考替代方案。`--apply` 永远不会执行替代方案。
- `migration_command`
  当前安装渠道不再被优先建议时的迁移路径
- `release_risk`
  从最新发布说明推断的轻量升级期变更风险摘要。它表示采用最新上游版本可能带来的行为变化或升级风险，而不是停留在当前版本的风险。

## 说明

- `--offline` 跳过依赖网络的最新版本查询，适合快速本地扫描。
- `--only-outdated` 仅显示既过期、又能在当前渠道升级的已安装工具。
- `--only-nonstandard` 将报告限定为安装渠道不符合厂商支持或推荐渠道的工具。
- `--only-class` 可将真正的 Agent CLI 与 `uv` 等相邻工具/运行时依赖分开。
- `--with-release-notes` 会在可行时获取最新发布说明并生成简单风险摘要。直接支持 GitHub Releases，也会以启发式方式概括少量厂商托管的 changelog 页面。
- 原生 self-update 不会自动优先于 npm 或 Homebrew。当 CLI 能明确保留其安装方式时，例如 Kilo Code 和 OpenCode 的 `--method`，计划会使用该原生命令。无法确认所有权影响时，计划保留现有 npm/Homebrew 渠道，仅将原生 self-update 作为信息性替代方案。
- Claude Code 使用 `claude update`；其安装器可能迁移安装类型，因此它不是 Homebrew 管理安装的默认选择。Codex 的 script 安装使用官方 standalone 安装器；app-bundled Codex 则通过 ChatGPT.app 更新。
- Antigravity CLI 使用 Google 的原生安装器安装至 `~/.local/bin/agy`，并在正常 CLI 使用期间运行已验证的后台 self-updater。审计会报告其官方 manifest 版本，但不会把交互式 `agy` 启动视为可由 `--apply` 自动执行的操作。
- 工具目录位于 `agent_cli_catalog.json`。
- 大多数条目为 `agent-cli`。当少量相邻工具对同一升级/治理工作流重要时，可作为 `tooling-runtime` 保留。
- 审计输出将选定的 `update_command` 与 `migration_command` 分开。`upgrade_guidance` 说明所选操作、它对安装所有权的影响，以及替代方案何时仅适用于审查或排障。
- `agent_cli_upgrade.py` 只升级既过期、又处于已识别 supported 或 recommended 渠道的条目。
- `agent_cli_upgrade.py --offline` 复用离线审计模式，速度更快但计划信息较不完整。
- `agent_cli_upgrade.py --only-class agent-cli` 将计划限制在与审计相同的类别边界。
- `agent_cli_upgrade.py --channel recommended` 仅生成厂商推荐安装渠道的计划。
- `agent_cli_upgrade.py --channel supported` 为默认值，同时包括 recommended 与 supported 渠道。
- `--recommended-only` 保留为 `--channel recommended` 的兼容别名。

## 为什么不使用 `topgrade`

`topgrade` 适合充当批量执行引擎，但本仓库解决的是不同问题。

`agent-cli-governor` 对以下方面具有明确策略：

- 工具是否通过厂商推荐渠道安装
- 当前渠道是厂商首选、仍受支持但非首选，还是已不在支持范围内
- 如何比较厂商特定的最新版本来源
- 安装方式不再被优先建议时如何提供迁移建议

这些政策检查是本项目的核心价值。通用升级器可以执行包管理器更新，但通常无法回答：

- 是否应该从当前渠道升级该工具
- 当前安装方式是否仍是厂商期望的方式
- 此工具需要渠道内升级还是迁移

因此，本项目将 `topgrade` 视为可选的执行基础设施，而不是决策层。

## 为什么区分 `recommended` 与 `supported`

这些状态描述的是当前安装渠道与厂商建议的关系，区分它们具有实际运维价值：

- `recommended`
  厂商当前优先建议的安装路径。它是日常升级最安全的默认值。
- `supported`
  厂商仍支持、但当前不是首选的安装渠道。
- `nonstandard`
  当前安装渠道不在厂商支持范围内，通常应先人工审查或迁移。

不做这一区分，本地 CLI 治理会过于粗糙：

- 部分工具会通过厂商不再优先建议的渠道升级
- 迁移机会会被隐藏在普通升级建议中
- 自动化检查无法区分“安全的默认升级”与“允许但不再优先的升级”

实际使用中：

- `--channel recommended` 是保守的每周升级路径
- `--channel supported` 是更广泛的审查路径
- `nonstandard` 表示当前渠道不在支持范围内，通常更适合迁移或人工审查

## 仓库开发

基础健康检查：

```bash
python3 -m py_compile agent_cli_audit.py agent_cli_upgrade.py gui.py
python3 agent_cli_audit.py --offline --only-class agent-cli
python3 agent_cli_upgrade.py --channel recommended
```

## 许可证

MIT。见 [LICENSE](./LICENSE)。
