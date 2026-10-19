# Skill Atlas · Codex 技能图谱

把本机 Codex 的 Skill 和会话放在同一张可交互图谱中。安装成 Skill 后，告诉 Agent「打开技能看板」，即可启动本地服务并在浏览器打开。

**本地运行 · 零第三方运行依赖 · MIT 开源**

## 安装

需要 **macOS 或 Linux、Python 3.9+、现代浏览器**。当前不支持原生 Windows（回收站使用 POSIX 文件锁）。Node.js 仅用于开发测试。无需 API Key、npm 或云服务。

### 让 Agent 安装

把以下指令交给支持 GitHub Skill 安装的 Agent：

> 从 https://github.com/jaykinhoo9/codex-skill-atlas 安装 `skills/codex-skill-atlas`，安装完成后使用这个 Skill 启动本地服务，并在浏览器中打开技能看板。

安装目录内的 `SKILL.md` 包含启动与打开浏览器的流程。Codex 若尚未发现新 Skill，重启会话后调用 `$codex-skill-atlas`。

### 从源码安装

```bash
git clone https://github.com/jaykinhoo9/codex-skill-atlas.git
cd codex-skill-atlas
python3 scripts/install.py
```

默认安装到 `${CODEX_HOME:-$HOME/.codex}/skills/codex-skill-atlas`，不会覆盖已有安装。也可使用 `--dest /path/to/agent/skills/codex-skill-atlas` 指定其他 Agent 的技能目录。

### 下载 ZIP

在 [Releases](https://github.com/jaykinhoo9/codex-skill-atlas/releases) 下载 `codex-skill-atlas-v1.0.0.zip`，把其中的 `codex-skill-atlas` 文件夹放入 Agent 的 skills 目录。发行包只包含这个工具自身的代码、Skill 指令、元数据与许可证。

## 启动浏览器页面

安装后对 Agent 说：

> 使用 $codex-skill-atlas，打开我的技能看板。

手动运行也可以：

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-skill-atlas/scripts/launch.py"
```

启动器会在系统浏览器打开页面。Agent 可加 `--no-open` 获取 JSON 中的 `url`，再用自己的浏览器工具打开。默认优先使用 8766 端口；占用时选用空闲端口，不会终止原服务。重复启动会复用匹配的实例。

```bash
# 停止当前安装启动的实例
python3 "${CODEX_HOME:-$HOME/.codex}/skills/codex-skill-atlas/scripts/launch.py" --stop
```

`--codex-home`、`--agents-home`、`--skills-root`、`--data-dir` 可指定当前机器的数据位置；停止服务时使用相同参数。更新前先停止，替换安装目录后重新启动；外部数据目录应保留。

## 功能

- 汇总 Codex 内置、本地技能、下载安装、插件缓存和项目技能，支持搜索与列表浏览。
- 点击节点查看来源、完整 SKILL.md、关联会话和读取次数；复制名称、路径与内容。
- 球体按累计读取次数放大；当前轮次和历史使用有不同流光效果。
- 默认显示最近 12 个会话，可切换运行中、全部会话或仅技能；会话多时技能分组向外展开。
- 按住空格拖动画布，松开后恢复操作；普通拖动框选节点，Shift 追加选择，滚轮缩放。
- 点击来源色点选择预设色或自定义色盘。配色保存在当前浏览器。
- 「低频使用」筛选累计读取少于 3 次（含 0 次）的技能。框选后可批量删除，**会话不删除**。
- 删除前确认实际目录及子技能范围，移入可恢复回收站；保留 30 天，到期永久清理。批量删除可整批恢复。
- 每 2 秒同步页面，技能目录约每 30 秒重扫；数据库缺失时仍可浏览本地技能。

## 隐私与运行数据

**GitHub 上是源代码，不是你的看板数据。页面运行在安装者自己的电脑上。** 不能用 GitHub Pages 托管完整功能，因为它需要本机 Python 服务读取本机文件。

- 服务只监听 `127.0.0.1`，不上传数据，不读取认证文件，不加载外部脚本、字体、图片或遥测。
- Codex 的 SQLite 数据库以只读方式打开；确认删除时只移动指定技能文件，会话数据库不写入。
- 安装包不含技能库存、会话标题、聊天记录、个人路径、使用记录、截图、密钥、缓存或回收站。
- 索引、日志、服务元数据与回收站统一写入 `$CODEX_HOME/skill-atlas`（默认 `~/.codex/skill-atlas`），**位于 Skill 安装目录之外**。
- 删除保留期为 30×24 小时；服务启动时及运行中每分钟检查，关机期间不执行，下次启动补做。恢复不会覆盖同名文件。跨磁盘移动等情况会报错并回滚。
- 内置、插件技能可能在应用或插件更新后重新出现。目录内子技能会在确认窗口中一并列出。运行中的看板不能删除自身。

## 识别范围与限制

扫描 `CODEX_HOME/skills`、`~/.agents/skills`、`/etc/codex/skills`、插件 manifest 声明的技能目录，以及已索引项目及祖先目录中的 `.agents/skills` / `.codex/skills`。同一插件缓存版本合并，符号链接去重。目录分类不代表作者身份或技能已启用。

会话索引依赖本机 `state_*.sqlite` 和 `thread_history_*.sqlite` 的兼容结构。仅旧版 JSONL、云端会话或另一台电脑上的记录不受支持。未来 Codex 数据库结构变化可能需要适配。

连线是成功读取 `SKILL.md` 的可观测证据，来源于 `commandExecution` 的读取动作及明确的只读命令；不是官方 Skill 调用遥测。搜索、列目录、聊天提及、失败调用与源码示例不计数。**没有连线不等于从未使用**；低频数量在首次索引期间会变化。

## 开发与发行

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.cjs
python3 scripts/check_release.py
python3 scripts/build_release.py
```

源码和 ZIP 都使用 `release-files.json` 的明确文件清单，构建不会递归复制工作目录。检查器拒绝未知的 Git 跟踪文件、符号链接、个人绝对路径与常见密钥特征。测试数据在临时目录内生成，不依赖发布者的数据。

- `skills/codex-skill-atlas/SKILL.md`：供 Agent 执行的入口。
- `skills/codex-skill-atlas/scripts/launch.py`：启动、实例复用、浏览器打开和停止。
- `skills/codex-skill-atlas/app/`：Python 本地服务与 Canvas 前端。
- `tests/`：读取识别、交互、颜色、删除恢复与启动安装测试。

许可证：[MIT](LICENSE)。
