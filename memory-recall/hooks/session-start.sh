#!/bin/sh
# memory-recall · SessionStart hook
# 作用: 新会话/恢复会话时, 把长期记忆的"精简版"自动注入上下文。
# 注意: 本脚本的 stdout 会被追加进对话上下文, 所以只允许输出 digest(≤18KB)。
#       诊断信息一律写 hook.log, 不要串到 stdout。
# 隐私: 不得硬编码用户名/绝对路径(2026-10-29 修正)。SKILL_DIR 由脚本自身位置推导,
#       HOME 取环境变量; hook.log 里的路径一律把用户目录缩写成 ~。

# 0) 定位技能目录: 由脚本自身路径推导, 不写死 C:/Users/<用户名>
SELF="$0"
case "$SELF" in
  */*) : ;;
  *) SELF="$(command -v "$SELF" 2>/dev/null || printf '%s' "$0")" ;;
esac
SELF_DIR="${SELF%/*}"
if [ "$SELF_DIR" = "$SELF" ]; then SELF_DIR="."; fi
SKILL_DIR=$(cd "$SELF_DIR/.." 2>/dev/null && pwd)
[ -n "$SKILL_DIR" ] || SKILL_DIR="$SELF_DIR/.."

LOG="$SKILL_DIR/hook.log"
HOME_DIR="${HOME:-$USERPROFILE}"
LOCALAPPDATA_DIR="${LOCALAPPDATA:-$HOME_DIR/AppData/Local}"

# 1) 选一个可用的 python(WorkBuddy 自带 → 系统安装目录 → PATH 上的 python3/python)
PY=""
for c in \
  "$HOME_DIR"/.workbuddy/binaries/python/versions/*/python.exe \
  "$LOCALAPPDATA_DIR"/Programs/Python/Python3*/python.exe
do
  if [ -f "$c" ]; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  for n in python3 python; do
    if command -v "$n" >/dev/null 2>&1; then PY="$n"; break; fi
  done
fi
if [ -z "$PY" ]; then
  printf '%s\n' "<<<AUTO-MEMORY-DIGEST | 注入失败>>>"
  printf '%s\n' "找不到可用的 python(已试 WorkBuddy 自带、系统安装目录、PATH 上的 python3/python)。"
  exit 0
fi

# 2) 定根目录: 优先平台给的工程目录, 否则当前目录
ROOT="${CODEBUDDY_PROJECT_DIR:-$PWD}"
if [ ! -d "$ROOT" ]; then ROOT="$PWD"; fi

# 3) 日志轮转(超过 200KB 就清空)
if [ -f "$LOG" ]; then
  SZ=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  if [ "$SZ" -gt 204800 ]; then : > "$LOG"; fi
fi

# 4) 跑 digest, stderr 进日志, stdout 留给上下文
out=$("$PY" "$SKILL_DIR/scripts/memory_bundle.py" --mode digest --root "$ROOT" 2>>"$LOG")
rc=$?

NBYTES=$(printf '%s' "$out" | wc -c | tr -d ' ')

# 5) 记日志前脱敏: 用户目录 → ~, 任何 C:/Users/<名字> 一律抹掉; python 只留文件名
#    (旧写法只 sed 了 $HOME_DIR 前缀, 当 HOME 与 python 真实路径不同源时会漏 —— 已修)
ROOT_LOG=$(printf '%s' "$ROOT" | sed -E 's#^'"$HOME_DIR"'#~#; s#[A-Za-z]:[/\\]Users[/\\][^/\\]*#~#g')
PY_LOG="${PY##*/}"
printf '%s\n' "--- $(date '+%Y-%m-%d %H:%M:%S') rc=$rc bytes=$NBYTES root=$ROOT_LOG py=$PY_LOG ---" >> "$LOG"

# 6) 失败兜底: 必须让模型知道"没有自动注入", 否则会静默丢记忆
#    rc!=0 才算失败; rc==0 且无输出 = 这台机器还没有任何记忆文件, 属正常情况, 静默退出
if [ "$rc" -ne 0 ]; then
  printf '%s\n' "<<<AUTO-MEMORY-DIGEST | 注入失败>>>"
  printf '%s\n' "长期记忆自动注入失败(rc=$rc)。本次会话开始前, 请先手动执行技能 memory-recall 读取三层记忆。"
  printf '%s\n' "诊断日志: ${LOG#"$HOME_DIR"}"
  exit 0
fi

[ -n "$out" ] || exit 0

printf '%s\n' "$out"
exit 0
