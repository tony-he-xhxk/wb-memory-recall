#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_bundle.py - 分层定位并打包 WorkBuddy 长期记忆。

三层结构:
  L1 用户级长期记忆  ~/.workbuddy/<user>-personal/MEMORY.md      (跨项目)
  L2 项目级长期记忆  <root>/.workbuddy/memory/MEMORY.md           (当前项目)
  L3 最近日报        <root>/.workbuddy/memory/YYYY-MM-DD.md       (按日期倒序)

用法:
  python memory_bundle.py --mode locate [--root DIR] [--home DIR]
  python memory_bundle.py --mode digest [--root DIR] [--max-bytes 18000]   (hook 用, 精简版)
  python memory_bundle.py --mode pack   [--root DIR] [--home DIR] [--days 3] [--headings]
                                        [--part N] [--max-part-bytes 24000]
                                        [--max-daily-bytes 40000] [--wrap 1200]

分片说明(重要):
  单次 stdout 超过约 30 KB 会被上层平台截断成"已持久化到文件"的提示,
  正文根本进不了上下文。因此 pack 默认按 --max-part-bytes 切分,
  先返回第 1 片并在首行写明 PART 1/N, 剩余片必须依次调用 --part 2..N 才读全。

退出码:
  0  正常
  1  参数或运行错误
  2  L1 用户级记忆缺失(其余层仍会输出)
"""

import argparse
import glob
import io
import json
import os
import re
import sys
from datetime import datetime, date

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


HOME = os.path.expanduser("~")
DATED_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


def human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def short_path(p):
    """把用户主目录缩写为 ~。

    输出会进入对话上下文与 hook.log，直接打印绝对路径会把本机用户名
    （C:/Users/<用户名>）一起带出去，分享截图/日志时就泄露了。2026-10-29 修正。
    """
    try:
        if p:
            s = str(p).replace("\\", "/")
            h = str(HOME).replace("\\", "/").rstrip("/")
            if h and s.lower().startswith(h.lower()):
                return "~" + s[len(h):]
    except Exception:
        pass
    return str(p)


def u8len(s):
    return len(s.encode("utf-8"))


def age_text(mtime):
    delta = datetime.now().timestamp() - mtime
    if delta < 3600:
        return f"{max(int(delta // 60), 1)} 分钟前"
    if delta < 86400:
        return f"{delta / 3600:.1f} 小时前"
    return f"{delta / 86400:.1f} 天前"


def file_info(path, label):
    st = os.stat(path)
    return {
        "label": label,
        "path": path,
        "exists": True,
        "bytes": st.st_size,
        "mtime": st.st_mtime,
        "mtime_text": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "age_text": age_text(st.st_mtime),
    }


def find_user_memory():
    """返回 (主副本 info|None, 旧副本列表)。"""
    candidates = []
    for p in glob.glob(os.path.join(HOME, ".workbuddy", "*-personal", "MEMORY.md")):
        if os.path.isfile(p):
            candidates.append(file_info(p, "user-level"))
    legacy = os.path.join(HOME, ".workbuddy", "MEMORY.md")
    if os.path.isfile(legacy):
        candidates.append(file_info(legacy, "user-level-legacy"))

    if not candidates:
        return None, []

    candidates.sort(key=lambda x: x["mtime"], reverse=True)
    primary = candidates[0]
    if len(candidates) == 1 and primary["label"] == "user-level-legacy":
        # 只存在平铺旧副本: 仍用它, 但必须让上层知道这是兜底而非权威
        primary["legacy_only"] = True

    stale = []
    for c in candidates[1:]:
        try:
            with open(c["path"], "rb") as f:
                same = f.read() == open(primary["path"], "rb").read()
        except Exception:
            same = False
        c["duplicate_of_primary"] = same
        stale.append(c)
    return primary, stale


def find_project_memory(root):
    p = os.path.join(root, ".workbuddy", "memory", "MEMORY.md")
    if os.path.isfile(p):
        return file_info(p, "project-level")
    return None


def describe_missing_project_memory(root):
    """L2 缺失时给出真实原因。

    旧版一律报「当前目录不是项目工作区」, 但当目录其实是工作区、只是还没建
    MEMORY.md 时就成了误报(2026-10-30 修正)。按目录实际状态分档:
      有 memory 目录且有日报   -> 只缺 MEMORY.md
      有 memory 目录但为空     -> 目录空
      有 .workbuddy 无 memory  -> 尚未建 memory 子目录
      连 .workbuddy 都没有     -> 才是真的不是项目工作区
    """
    mem_dir = os.path.join(root, ".workbuddy", "memory")
    if os.path.isdir(mem_dir):
        n = 0
        try:
            for name in os.listdir(mem_dir):
                if DATED_RE.match(name) and os.path.isfile(os.path.join(mem_dir, name)):
                    n += 1
        except OSError:
            pass
        if n:
            return "是项目工作区, 已有 %d 份日报, 但尚未建立项目级 MEMORY.md" % n
        return "是项目工作区, 但 memory 目录为空(无 MEMORY.md 也无日报)"
    if os.path.isdir(os.path.join(root, ".workbuddy")):
        return "是项目工作区, 但尚无 .workbuddy/memory 目录"
    return "当前目录不是项目工作区(无 .workbuddy)"


def find_daily_logs(root, days):
    d = os.path.join(root, ".workbuddy", "memory")
    if not os.path.isdir(d):
        return []
    items = []
    for name in os.listdir(d):
        m = DATED_RE.match(name)
        if not m:
            continue
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        full = os.path.join(d, name)
        if not os.path.isfile(full):
            continue
        info = file_info(full, "daily")
        info["date"] = day.isoformat()
        info["name"] = name
        items.append(info)
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:days]


def build_manifest(root, days):
    primary, stale = find_user_memory()
    manifest = {
        "root": os.path.abspath(root),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "layer1": primary,
        "layer1_stale": stale,
        "layer2": find_project_memory(root),
        "layer3": find_daily_logs(root, days),
        "warnings": [],
    }
    manifest["layer2_note"] = (
        None if manifest["layer2"] else describe_missing_project_memory(root)
    )
    if primary is None:
        manifest["warnings"].append("L1 用户级长期记忆缺失: 未找到 ~/.workbuddy/*-personal/MEMORY.md")
    elif primary.get("legacy_only"):
        manifest["warnings"].append(
            "L1 只找到旧版平铺副本 %s (%s), 可能不是最新, 使用时标注 [权威性存疑]"
            % (primary["path"], primary["mtime_text"])
        )
    for s in stale:
        tag = "内容重复的副本" if s.get("duplicate_of_primary") else "较旧的副本"
        manifest["warnings"].append(
            f"{tag}已忽略: {s['path']} ({human_size(s['bytes'])}, {s['mtime_text']})"
        )
    if manifest["layer2"] is None:
        manifest["warnings"].append(
            f"L2 缺失: {root} 下无 .workbuddy/memory/MEMORY.md({manifest['layer2_note']})"
        )
    if not manifest["layer3"]:
        manifest["warnings"].append(f"L3 缺失: {root} 下无 .workbuddy/memory/YYYY-MM-DD.md 日报")
    return manifest


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def wrap_long_lines(text, width):
    """折行超长行, 避免上层 Read 工具按 2000 字符/行截断而静默丢内容。"""
    out = []
    for line in text.splitlines():
        if len(line) <= width:
            out.append(line)
            continue
        while len(line) > width:
            out.append(line[:width] + " \\")
            line = line[width:]
        out.append(line)
    return "\n".join(out)


def headings_of(text, limit=60):
    hs = []
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^#{1,3}\s+\S", line):
            hs.append(f"  L{i:>4}  {line.rstrip()}")
            if len(hs) >= limit:
                hs.append(f"  ...(超过 {limit} 条, 索引已截断)")
                break
    return hs


def build_bundle(root, days, max_daily_bytes, wrap_width, want_headings):
    """把三层记忆拼成一整段文本, 返回 (bundle_text, has_l1)。"""
    manifest = build_manifest(root, days)
    buf = []

    def w(s=""):
        buf.append(s)

    w("=" * 74)
    w("MEMORY BUNDLE  根目录: %s" % short_path(manifest["root"]))
    w("生成时间: %s" % manifest["now"])
    w("=" * 74)
    w()
    w("[REMINDER] 回执里的 [GOTCHA] 只能取自下面 <<<LAYER 1..3>>> 之间的记忆正文。")
    w("           技能 SKILL.md 里的「规则/禁止/反例」是执行纪律, 不是记忆内容 ——")
    w("           把它们列成 GOTCHA 等于交白卷, 必须重列。")
    w()

    # ---- L1 ----
    l1 = manifest["layer1"]
    if l1:
        text = read_text(l1["path"])
        w("<<<LAYER 1 | 用户级长期记忆(跨项目, 最高优先级)>>>")
        w("path : %s" % l1["path"])
        if l1.get("legacy_only"):
            w("state: 旧版平铺副本, 权威性存疑")
        w("size : %s | 修改: %s (%s) | 行数: %d"
          % (human_size(l1["bytes"]), l1["mtime_text"], l1["age_text"], text.count("\n") + 1))
        if want_headings:
            w("--- 标题索引 ---")
            w("\n".join(headings_of(text)))
        w("--- 正文开始 ---")
        w(wrap_long_lines(text, wrap_width).rstrip())
        w("--- 正文结束 ---")
        w(">>>")
        w()
    else:
        w("<<<LAYER 1 | 用户级长期记忆>>>")
        w("[缺失] 未找到 ~/.workbuddy/*-personal/MEMORY.md")
        w(">>>")
        w()

    for s in manifest["layer1_stale"]:
        w("[忽略副本] %s (%s, %s) — %s"
          % (s["path"], human_size(s["bytes"]), s["mtime_text"],
             "内容与主副本相同" if s.get("duplicate_of_primary") else "旧副本, 不读取"))
    if manifest["layer1_stale"]:
        w()

    # ---- L2 ----
    l2 = manifest["layer2"]
    if l2:
        text = read_text(l2["path"])
        w("<<<LAYER 2 | 项目级长期记忆(当前项目约定)>>>")
        w("path : %s" % l2["path"])
        w("size : %s | 修改: %s (%s)" % (human_size(l2["bytes"]), l2["mtime_text"], l2["age_text"]))
        if want_headings:
            w("--- 标题索引 ---")
            w("\n".join(headings_of(text)))
        w("--- 正文开始 ---")
        w(wrap_long_lines(text, wrap_width).rstrip())
        w("--- 正文结束 ---")
        w(">>>")
        w()
    else:
        w("<<<LAYER 2 | 项目级长期记忆>>>")
        w("[缺失] %s 下无 .workbuddy/memory/MEMORY.md" % manifest["root"])
        w(">>>")
        w()

    # ---- L3 ----
    logs = manifest["layer3"]
    if logs:
        w("<<<LAYER 3 | 最近日报(%d 篇, 按日期倒序)>>>" % len(logs))
        used = 0
        for item in logs:
            text = read_text(item["path"])
            raw = u8len(text)
            if used + raw > max_daily_bytes:
                w("[跳过] %s (%s) — 超出日报预算 %s, 只列文件名"
                  % (item["name"], human_size(raw), human_size(max_daily_bytes)))
                continue
            used += raw
            w("--- %s | %s | %s ---" % (item["name"], human_size(raw), item["mtime_text"]))
            w(wrap_long_lines(text, wrap_width).rstrip())
        w(">>>")
        w()
    else:
        w("<<<LAYER 3 | 最近日报>>>")
        w("[缺失] 无 YYYY-MM-DD.md 日报")
        w(">>>")
        w()

    if manifest["warnings"]:
        w("<<<WARNINGS>>>")
        for m in manifest["warnings"]:
            w("- " + m)
        w(">>>")

    return "\n".join(buf) + "\n", l1 is not None


def split_parts(text, max_bytes):
    """按空行边界切分, 不把一段话劈成两半; 单块超限时再按行硬切。"""
    blocks = re.split(r"\n[ \t]*\n", text)
    units = []
    for b in blocks:
        if u8len(b) + 2 <= max_bytes:
            units.append(b)
            continue
        cur_lines, cur_bytes = [], 0
        for line in b.split("\n"):
            nb = u8len(line) + 1
            if cur_lines and cur_bytes + nb > max_bytes:
                units.append("\n".join(cur_lines))
                cur_lines, cur_bytes = [], 0
            cur_lines.append(line)
            cur_bytes += nb
        if cur_lines:
            units.append("\n".join(cur_lines))

    parts, cur, cur_bytes = [], [], 0
    for u in units:
        nb = u8len(u) + 2
        if cur and cur_bytes + nb > max_bytes:
            parts.append("\n\n".join(cur))
            cur, cur_bytes = [], 0
        cur.append(u)
        cur_bytes += nb
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def split_sections(text):
    """按二级标题(##)切节, 返回 [(title, body), ...]; 首个为前言节。"""
    sections, cur_title, cur_lines = [], "(前言)", []
    for line in text.splitlines():
        if re.match(r"^##\s+\S", line):
            sections.append((cur_title, "\n".join(cur_lines).strip()))
            cur_title, cur_lines = line.lstrip("# ").strip(), [line]
        else:
            cur_lines.append(line)
    sections.append((cur_title, "\n".join(cur_lines).strip()))
    return [(t, b) for t, b in sections if b]


HOT_TITLE = ("铁律", "踩坑", "教训", "禁止", "必须", "归因", "别再", "陷阱", "事故", "不要再",
             "纠正", "要求", "实测", "权威", "确认")
HOT_BODY = ("踩坑", "铁律", "禁止", "不要在", "别用", "别再", "归因", "翻车", "越界")


def score_section(title, body):
    s = 0
    if "★" in title:
        s += 100
    for kw in HOT_TITLE:
        if kw in title:
            s += 30
            break
    hits = sum(1 for kw in HOT_BODY if kw in body)
    s += min(hits * 8, 40)
    if title == "(前言)":
        s += 300
    return s


PER_SECTION_CAP = 1200   # 单节最多占多少字节(超出截断, 规则通常写在节首)


def fit_body(body, cap=PER_SECTION_CAP):
    b = body.encode("utf-8")
    if len(b) <= cap:
        return body
    return b[:cap].decode("utf-8", "ignore") + "\n…[本节已截断, 全文用 /memory-recall]"


def pick_sections(text, budget):
    """按含金量挑选章节: 同分先塞短的, 单节超长截断, 让更多高价值节挤进来。"""
    sections = split_sections(text)
    ranked = sorted(
        enumerate(sections),
        key=lambda x: (-score_section(*x[1]), u8len(fit_body(x[1][1])), x[0]),
    )
    chosen, used = [], 0
    for idx, (title, body) in ranked:
        fitted = fit_body(body)
        cost = u8len(fitted) + 8
        if used + cost > budget:
            continue
        chosen.append((idx, title, fitted))
        used += cost
    chosen.sort(key=lambda x: x[0])
    omitted = [t for i, (t, _) in enumerate(sections) if i not in {c[0] for c in chosen}]
    return [(t, b) for _, t, b in chosen], omitted, len(sections)


def build_digest(root, days, max_bytes):
    """会话开头的精简注入版: 只挑含金量最高的章节, 明确列出被省略的部分。"""
    manifest = build_manifest(root, days)
    if not manifest["layer1"] and not manifest["layer2"] and not manifest["layer3"]:
        return ""      # 一条记忆都没有(新机器/新用户): 静默, 不要每个会话都注入噪音
    out = []
    overhead = 1100          # 头部指令 + 分隔符预算
    l2_cap, l3_cap = 2500, 2500
    l1_cap = max(max_bytes - overhead - l2_cap - l3_cap, 3000)

    l1 = manifest["layer1"]
    omitted_note = "无"
    if l1:
        text = read_text(l1["path"])
        picked, omitted, total = pick_sections(text, l1_cap)
        omitted_note = ("%d/%d 节未注入" % (len(omitted), total)) if omitted else ("%d 节全注入" % total)
    else:
        picked, omitted, total, text = [], [], 0, ""

    out.append("<<<AUTO-MEMORY-DIGEST | 由 SessionStart hook 自动注入>>>")
    out.append("时间: %s | 根目录: %s" % (manifest["now"], short_path(manifest["root"])))
    if l1:
        out.append("L1: %s (%s, 修改 %s) | 本摘要 %s"
                   % (l1["path"], human_size(l1["bytes"]), l1["mtime_text"], omitted_note))
        if l1.get("legacy_only"):
            out.append("注意: 只有旧版平铺副本, 权威性存疑")
    else:
        out.append("L1: [缺失] 未找到 ~/.workbuddy/*-personal/MEMORY.md")
    if manifest["layer2"]:
        out.append("L2: %s (%s)" % (manifest["layer2"]["path"], human_size(manifest["layer2"]["bytes"])))
    else:
        out.append("L2: 无 —— %s" % manifest["layer2_note"])
    if manifest["layer3"]:
        out.append("L3: %s" % " / ".join(x["name"] for x in manifest["layer3"]))
    else:
        out.append("L3: 无日报")
    out.append("")
    out.append("[REMINDER] [GOTCHA] 只能从下面的记忆正文里取, 不能从技能 SKILL.md 的规则里取。")
    out.append("")
    out.append("这是【精简版】, 不是全部记忆。上文若省略了章节, 且本次任务涉及它们, 或即将改动")
    out.append("用户的文件/配置/文档, 必须先跑 /memory-recall 读全文(分 3 片)。禁止仅凭本摘要")
    out.append("断言\"记忆里没写过\"。")
    if omitted:
        tail = "、".join(t[:18] for t in omitted[:12]) + ("…" if len(omitted) > 12 else "")
        out.append("被省略的章节: " + tail)
    out.append("<<<以下为记忆原文节选>>>")
    out.append("")

    for title, body in picked:
        out.append(body)
        out.append("")
    if omitted:
        out.append("[省略 %d 节] 需要时用 /memory-recall 读全文" % len(omitted))
        out.append("")

    l2 = manifest["layer2"]
    if l2:
        text2 = read_text(l2["path"])
        out.append("<<<LAYER 2 | 项目级记忆>>>")
        if u8len(text2) <= l2_cap:
            out.append(wrap_long_lines(text2, 1200))
        else:
            picked2, omitted2, _ = pick_sections(text2, l2_cap)
            for _, body in picked2:
                out.append(body)
            out.append("[项目记忆超预算, 省略 %d 节]" % len(omitted2))
        out.append("")

    if manifest["layer3"]:
        newest = manifest["layer3"][0]
        text3 = read_text(newest["path"])
        out.append("<<<LAYER 3 | 最近日报 %s>>>" % newest["name"])
        if u8len(text3) <= l3_cap:
            out.append(wrap_long_lines(text3, 1200))
        else:
            out.append(wrap_long_lines(text3[: l3_cap // 3], 1200))
            out.append("[日报超预算, 已截断, 完整内容用 /memory-recall]")
        out.append("")

    out.append("<<<END DIGEST>>>")
    return "\n".join(out) + "\n"


def _home_forms():
    """HOME 的各种写法：反斜杠、正斜杠、git-bash 的 /c/Users/xxx。"""
    h = str(HOME)
    fwd = h.replace("\\", "/")
    forms = {h, fwd, fwd.replace("/", "\\")}
    m = re.match(r"^([A-Za-z]):/(.*)$", fwd)
    if m:
        forms.add("/" + m.group(1).lower() + "/" + m.group(2))
    return sorted((x for x in forms if x and len(x) > 2), key=len, reverse=True)


def sanitize(text):
    """输出边界统一脱敏：把用户主目录替换成 ~。

    本脚本的输出会进入对话上下文（digest 每会话自动注入），直接打印绝对路径
    会把本机用户名（C:/Users/<用户名>）带出去，分享截图/日志即泄露。2026-10-29 修正。
    """
    if not text:
        return text
    s = str(text)
    for h in _home_forms():
        s = s.replace(h, "~")
    # 只把 ~ 后面紧跟的那个分隔符统一成正斜杠，不动正文里其它反斜杠
    s = s.replace("~\\", "~/")
    return s


def sanitize_obj(o):
    """递归脱敏 dict/list 里的字符串（locate 模式的 JSON 要先脱敏再序列化，
    否则 json.dumps 把反斜杠转义成 \\\\ 后，路径就匹配不上了）。"""
    if isinstance(o, dict):
        return {k: sanitize_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [sanitize_obj(v) for v in o]
    if isinstance(o, str):
        return sanitize(o)
    return o


def emit(text):
    """唯一的 stdout 出口，保证脱敏。新增输出必须走这里，不要直接 print。"""
    sys.stdout.write(sanitize(text))


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--mode", choices=["locate", "pack", "digest"], default="locate")
    ap.add_argument("--root", default=os.getcwd(), help="项目根目录, 默认当前目录")
    ap.add_argument("--home", default=None, help="覆盖用户主目录, 默认 ~ (仅校验时用)")
    ap.add_argument("--days", type=int, default=3, help="回看最近几篇日报, 默认 3")
    ap.add_argument("--max-daily-bytes", type=int, default=40000, help="日报总字节预算, 默认 40000")
    ap.add_argument("--wrap", type=int, default=1200, help="长行折行宽度, 默认 1200")
    ap.add_argument("--headings", action="store_true", help="pack 时附带标题索引")
    ap.add_argument("--part", type=int, default=1, help="输出第几片, 从 1 开始")
    ap.add_argument("--max-part-bytes", type=int, default=24000, help="单片上限, 默认 24000")
    ap.add_argument("--max-bytes", type=int, default=18000, help="digest 总预算, 默认 18000")
    args = ap.parse_args()

    root = args.root
    if not os.path.isdir(root):
        sys.stderr.write("ERROR: --root 不是目录: %s\n" % root)
        return 1

    if args.home:
        if not os.path.isdir(args.home):
            sys.stderr.write("ERROR: --home 不是目录: %s\n" % args.home)
            return 1
        global HOME
        HOME = os.path.abspath(args.home)

    if args.mode == "locate":
        m = build_manifest(root, args.days)
        m["layer1_stale"] = [{k: v for k, v in s.items() if k != "label"} for s in m["layer1_stale"]]
        emit(json.dumps(sanitize_obj(m), ensure_ascii=False, indent=2) + "\n")
        return 0 if m["layer1"] else 2

    if args.mode == "digest":
        emit(build_digest(root, args.days, args.max_bytes))
        return 0

    bundle, has_l1 = build_bundle(root, args.days, args.max_daily_bytes, args.wrap, args.headings)
    parts = split_parts(bundle, args.max_part_bytes)
    n = len(parts)
    idx = args.part
    if idx < 1 or idx > n:
        sys.stderr.write("ERROR: --part 取值 1..%d, 收到 %d\n" % (n, idx))
        return 1

    total = u8len(bundle)
    w = emit
    if n > 1:
        w("### PART %d/%d | 本片 %s | 全文 %s%s\n"
          % (idx, n, human_size(u8len(parts[idx - 1])), human_size(total),
             (" | 读完本片后继续 --part %d" % (idx + 1)) if idx < n else " | 最后一片"))
        if idx == 1:
            w("### 提示: 共 %d 片, 必须把 1..%d 全部读完才算读进上下文; 只看第 1 片等于没读。\n" % (n, n))
    else:
        w("### PART 1/1 | 全文 %s\n" % human_size(total))
    w(parts[idx - 1].rstrip() + "\n")

    if idx < n:
        w("\n### [未读完] 还剩 %d 片, 下一片: --part %d\n" % (n - idx, idx + 1))
    else:
        w("\n### [读完] %d/%d 片全部输出完毕\n" % (n, n))
    return 0 if has_l1 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
