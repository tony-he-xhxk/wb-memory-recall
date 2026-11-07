# memory-recall

给 WorkBuddy 用的技能：把**三层长期记忆**（用户级 `MEMORY.md` / 项目级 `MEMORY.md` / 最近几日日报）在**会话开头自动灌进上下文**，需要时再读全文。

## 它解决什么

WorkBuddy 每轮注入的长期记忆是**截断版**，而「踩坑」条目常常正好被截断，于是同一个错误被反复踩。本技能做两件事：

1. **自动注入**：`SessionStart` hook 在开会话时输出一份 ≤18 KB 的精简版，按 `★` / 铁律 / 踩坑 / 纠正 加权挑章节，并明确列出哪些章节被省略。
2. **按需读全**：输入 `/memory-recall` 时分层定位 + 分片读完全文（55 KB 级别也不会被平台截断）。

只读。不改你的记忆文件，不联网，唯一的写入是技能目录下的 `hook.log`（诊断用，可随时删）。

## 安装

```bash
# 1. 放到用户级技能目录（Windows / macOS / Linux 都一样）
cp -r memory-recall ~/.workbuddy/skills/
```

也可以通过WorkBuddy的图形化界面安装。



2. 注册会话启动钩子——编辑 `~/.workbuddy/settings.json`，在顶层加 `hooks`（已有就合并）：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$HOME/.workbuddy/skills/memory-recall/hooks/session-start.sh\"",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

3. **重启 WorkBuddy**。钩子只在启动时读取配置，改完不热加载。

不需要自动注入可以跳过第 2、3 步，只用 `/memory-recall` 手动唤起。

## 自检

```bash
bash "$HOME/.workbuddy/skills/memory-recall/hooks/session-start.sh" | wc -c
```

判据**不是**绝对字节数（记忆多少、选了几节，输出就多大），看这三条：

- 输出 **≠ 0** 且不含 `注入失败` → 正常。字节数一般落在「三层记忆总量」的 70–90%，上限 18 KB（`--max-bytes` 可调）。记忆只有 7 KB 时输出 6.5 KB 完全正常。
- 输出 **= 0** → 这台机器还没有任何记忆文件（正常，不会注入噪音）。
- 出现 `<<<AUTO-MEMORY-DIGEST | 注入失败>>>` → 看 `~/.workbuddy/skills/memory-recall/hook.log` 里的 `rc=`。

## 依赖

- `python3`（或 `python`）。脚本只用标准库。
- Windows 上钩子由 Git Bash 执行（WorkBuddy 强制），路径分隔符无关紧要。

## 卸载

```bash
rm -rf ~/.workbuddy/skills/memory-recall
```

再把 `settings.json` 里的 `hooks.SessionStart` 删掉或设为 `[]`，重启。

## 文件

| 文件                         | 作用                                         |
| -------------------------- | ------------------------------------------ |
| `SKILL.md`                 | 技能本体：规范、四步流程、隐私约定、反例                       |
| `scripts/memory_bundle.py` | `locate`（元信息）/ `digest`（精简版）/ `pack`（全文分片） |
| `hooks/session-start.sh`   | 会话启动钩子入口                                   |
