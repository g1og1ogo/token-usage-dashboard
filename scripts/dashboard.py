#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token 消耗看板 — 从 WorkBuddy/CodeBuddy 本地会话日志生成单文件离线 HTML 看板。

设计要点：
  * 零第三方依赖，仅标准库
  * 单遍扫描 projects/**/*.jsonl，同时做「业务归因」与「用量累计」
  * usage 字段多形态兼容（OpenAI / Anthropic / 各家中转商命名不一致）
  * light / dark 双主题，light 为默认且可直接打印
  * 业务主线词典外置 JSON，不同用户/不同项目可换
  * 可选导出结构化 JSON，便于二次分析或跨期对比

用法见 --help。
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from html import escape as E

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


VERSION = "1.0.0"

HOME = os.path.expanduser("~")
DEFAULT_PROJECTS = os.path.join(HOME, ".workbuddy", "projects")
DEFAULT_AUDIT = os.path.join(HOME, ".workbuddy", "audit-log")


# ---------------------------------------------------------------- 默认业务词典
# 仅作兜底：正式使用请把自己的业务主线写进 JSON 配置并用 --biz-config 指定。
DEFAULT_BIZ: dict[str, list[str]] = {
    "工具/基建": ["skill", "skills", "SKILL", "publish", "workbuddy", "mcp", "binaries", "dashboard"],
    "未归类": [],
}


# ---------------------------------------------------------------- 字段兼容层
def _first(d: dict, *keys, default=0):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return default


def _usage_of(msg: dict) -> dict | None:
    """从一条 assistant 消息里取出 usage 结构。兼容多种埋点形态。"""
    pd = msg.get("providerData")
    if isinstance(pd, dict):
        for k in ("usage", "rawUsage", "tokenUsage", "tokens"):
            u = pd.get(k)
            if isinstance(u, dict) and u:
                return u
    # 少数版本把 usage 挂在消息顶层
    for k in ("usage", "rawUsage"):
        u = msg.get(k)
        if isinstance(u, dict) and u:
            return u
    return None


def _model_of(msg: dict) -> str:
    pd = msg.get("providerData") or {}
    if not isinstance(pd, dict):
        pd = {}
    for k in ("requestModelName", "model", "modelName", "requestModel"):
        v = pd.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = msg.get("model")
    return v.strip() if isinstance(v, str) and v.strip() else "unknown"


def _detail_token(detail_list, *keys) -> int:
    """cached_tokens / cache_read_input_tokens / reasoning_tokens 等散iage在 details 数组里。"""
    if not isinstance(detail_list, list):
        return 0
    for d in detail_list:
        if isinstance(d, dict):
            v = _first(d, *keys, default=0)
            if v:
                return v
    return 0


def extract_usage(msg: dict) -> dict | None:
    """返回统一的 usage 记录，取不到返回 None。"""
    u = _usage_of(msg)
    if not u:
        return None
    it = _first(u, "inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
    ot = _first(u, "outputTokens", "output_tokens", "completionTokens", "completion_tokens")
    cached = _first(
        u,
        "cachedTokens",
        "cached_tokens",
        "cacheReadInputTokens",
    ) or _detail_token(u.get("inputTokensDetails") or u.get("prompt_tokens_details"),
                       "cached_tokens", "cachedTokens", "cache_read_input_tokens")
    reasoning = _first(u, "reasoningTokens", "reasoning_tokens") or _detail_token(
        u.get("outputTokensDetails") or u.get("completion_tokens_details"),
        "reasoning_tokens", "reasoningTokens", "thinking_tokens",
    )
    if it == 0 and ot == 0:
        return None
    return {"input": it, "output": ot, "cached": cached, "reasoning": reasoning}


# ---------------------------------------------------------------- 时间处理
def _ts_to_ms(ts) -> int:
    """日志里 timestamp 可能是秒、毫秒或 ISO 字符串，统一成毫秒。"""
    if isinstance(ts, (int, float)):
        v = int(ts)
        return v if v > 1e11 else v * 1000
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        try:
            return int(dt.datetime.fromisoformat(s).timestamp() * 1000)
        except Exception:
            return 0
    return 0


# 注入块 = 标签名里含下划线或连字符的成对 XML 标签（system-reminder / user_info /
# project_context / local-command-stdout ...）。标准 HTML 标签（div/p/table/...）都不含这两个
# 字符，因此这条规则既能穷举未来的新注入标签，又不会误伤正文里的 HTML 片段。
INJECT_PAIR_RE = re.compile(r"<\s*([a-zA-Z][\w]*[_-][\w]*)\b[^>]*>.*?<\s*/\s*\1\s*>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
INJECT_OPEN_RE = re.compile(r"<\s*([a-zA-Z][\w]*[_-][\w]*)\b[^>]*>")

# 用户真正的发言被包在 <user_query> 里，紧跟在 system-reminder 之后。
# 它同样含下划线，所以必须先于注入块剔除，否则会被通用规则一并删掉。
USER_QUERY_RE = re.compile(r"<\s*user[_-]?query\s*[^>]*>(.*?)</\s*user[_-]?query\s*>", re.S | re.I)

SYS_HINTS = (
    "identity files", "You are WorkBuddy", "connector-status", "agent-mail Agent Mail",
    "OS Version:", "IDE Theme:", "Workspace Folder:", "IMPORTANT:", "Tool request object",
    "memory_and_skills", "Below are", "The following", "Task Objective", "Current Response",
)


def _looks_like_system(s: str) -> bool:
    """日志里 system prompt 片段有时挂在 role=user 上，这里做启发式排除。"""
    if any(h in s for h in SYS_HINTS):
        return True
    if len(s) > 180 and re.match(r"^(You are|Below|The following|IMPORTANT|#|Notes:)", s, re.I):
        return True
    return False


def strip_meta(text: str) -> str:
    """剥离宿主注入的系统块，还原真实的用户/助手表达文本。
    归因仍用原文（注入块里的文件名和时间戳是有效信号），但展示必须用剥离后的结果。"""
    t = text
    for _ in range(6):  # 处理嵌套
        new = INJECT_PAIR_RE.sub(" ", t)
        if new == t:
            break
        t = new
    # 文本被截断导致注入标签未闭合时，从该标签处起整体丢弃
    for m in INJECT_OPEN_RE.finditer(t):
        if not re.search(r"</\s*" + re.escape(m.group(1)) + r"\s*>", t[m.end():]):
            t = t[: m.start()]
            break
    t = TAG_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


FILE_RE = re.compile(r"[\w一-鿿\-\./\\]+\.(?:html|docx|pptx|xlsx|md|pdf|py|js|json|png|jpg|csv|txt)")


# ---------------------------------------------------------------- 采集
def load_biz_rules(path: str | None) -> dict[str, list[str]]:
    if not path:
        return dict(DEFAULT_BIZ)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rules = data.get("biz") if isinstance(data, dict) and "biz" in data else data
    if not isinstance(rules, dict):
        raise SystemExit(f"[x] 业务词典格式错误：期望 object，实际 {type(rules).__name__}（{path}）")
    return {k: [str(w) for w in v] for k, v in rules.items() if isinstance(v, list)}


def attribution_weights(text: str, biz: dict[str, list[str]], filename_weight: float, kw_weight: float) -> Counter:
    """单条消息的业务归因打分：文件名命中为强信号，关键词命中为弱信号。"""
    hits: Counter = Counter()
    low = text.lower()
    for name in FILE_RE.findall(text):
        base = os.path.basename(name).lower()
        for b, kws in biz.items():
            if b == "未归类":
                continue
            if any(kw.lower() in base for kw in kws):
                hits[b] += filename_weight
                break
    for b, kws in biz.items():
        if b == "未归类":
            continue
        if any(kw.lower() in low for kw in kws):
            hits[b] += kw_weight
    return hits


def collect(args, biz: dict[str, list[str]]) -> dict:
    files = sorted(glob.glob(os.path.join(args.projects_root, "*", "*.jsonl")))
    if args.include_root:
        files += sorted(glob.glob(os.path.join(args.projects_root, "*.jsonl")))
    if not files:
        print(f"[!] 未找到会话日志：{args.projects_root}", file=sys.stderr)

    since_ms = _ts_to_ms(dt.datetime.strptime(args.since, "%Y-%m-%d").timestamp()) if args.since else 0
    until_ms = _ts_to_ms(dt.datetime.strptime(args.until, "%Y-%m-%d").timestamp()) + 86399_000 if args.until else 0

    sess_hits: dict[str, Counter] = defaultdict(Counter)
    sess_first_user: dict[str, str] = {}
    sess_last_ts: dict[str, int] = defaultdict(int)

    records: list[dict] = []
    skipped_lines = 0
    skipped_files = 0

    for fp in files:
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        d = json.loads(ln)
                    except Exception:
                        skipped_lines += 1
                        continue
                    if not isinstance(d, dict) or d.get("type") != "message":
                        continue
                    role = d.get("role")
                    sess = d.get("sessionId") or os.path.basename(fp)[:8] or "_"
                    ts = _ts_to_ms(d.get("timestamp"))
                    if ts and ((since_ms and ts < since_ms) or (until_ms and ts > until_ms)):
                        continue
                    if ts:
                        sess_last_ts[sess] = max(sess_last_ts[sess], ts)

                    text = flatten_text(d.get("content"), args.text_limit)

                    if role == "user":
                        if sess not in sess_first_user:
                            cand = pick_user_intent(d.get("content"))
                            if cand:
                                sess_first_user[sess] = cand[:120]
                        # 用户原话是最干净的归因信号，权重加倍计入
                        q = user_query_of(d.get("content"))
                        if q:
                            hits = attribution_weights(q, biz, args.filename_weight,
                                                       args.kw_weight * args.user_boost)
                            if hits:
                                sess_hits[sess].update(hits)
                        continue

                    if role != "assistant":
                        continue

                    hits = attribution_weights(text, biz, args.filename_weight, args.kw_weight)
                    if hits:
                        sess_hits[sess].update(hits)

                    u = extract_usage(d)
                    if not u:
                        continue
                    u.update(sess=sess, ts=ts, model=_model_of(d))
                    records.append(u)
        except OSError as e:
            skipped_files += 1
            print(f"[!] 跳过不可读文件 {fp}: {e}", file=sys.stderr)

    # session -> 业务主线（投票 top1）
    sess_biz: dict[str, str] = {}
    for sess, hits in sess_hits.items():
        if not hits:
            continue
        top_biz, top_score = hits.most_common(1)[0]
        if top_score >= args.min_score:
            sess_biz[sess] = top_biz

    # 归因落地
    agg = new_agg()
    agg["unmatched"] = 0
    sess_stat: dict[str, dict] = defaultdict(lambda: new_sess_node())

    for r in records:
        agg["model_input"][r["model"]] += r["input"]
        agg["model_output"][r["model"]] += r["output"]
        agg["model_cached"][r["model"]] += r["cached"]
        agg["model_reasoning"][r["model"]] += r["reasoning"]
        agg["model_count"][r["model"]] += 1

        day = dt.datetime.fromtimestamp(r["ts"] / 1000).strftime("%Y-%m-%d") if r["ts"] else "未知"
        agg["day_total"][day] += r["input"] + r["output"]

        node = sess_stat[r["sess"]]
        node["in"] += r["input"]
        node["out"] += r["output"]
        node["reasoning"] += r["reasoning"]
        node["count"] += 1
        node["models"][r["model"]] += 1
        if r["ts"]:
            node["last"] = max(node["last"], r["ts"])
            node["first"] = min(node["first"], r["ts"]) if node["first"] else r["ts"]

        b = sess_biz.get(r["sess"])
        if b is None:
            agg["unmatched"] += 1
            continue
        agg["biz_input"][b] += r["input"]
        agg["biz_output"][b] += r["output"]
        agg["biz_cached"][b] += r["cached"]
        agg["biz_reasoning"][b] += r["reasoning"]
        agg["biz_count"][b] += 1
        agg["biz_models"][b][r["model"]] += 1
        agg["biz_sessions"][b].add(r["sess"])
        agg["biz_day"][b][day] += r["input"] + r["output"]

    audit = collect_audit(args) if not args.no_audit else {}

    return {
        "agg": aggregate_to_plain(agg),
        "sessions": [
            {
                "sess": s,
                "biz": sess_biz.get(s, "未归因"),
                "first_msg": sess_first_user.get(s, ""),
                "last_ts": sess_last_ts.get(s, 0),
                **{k: v for k, v in n.items() if k != "models"},
                "top_model": n["models"].most_common(1)[0][0] if n["models"] else "",
            }
            for s, n in sess_stat.items()
        ],
        "audit": audit,
        "meta": {
            "version": VERSION,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "projects_root": args.projects_root,
            "audit_dir": "" if args.no_audit else args.audit_dir,
            "files_scanned": len(files),
            "skipped_files": skipped_files,
            "skipped_lines": skipped_lines,
            "records": len(records),
            "sessions_total": len(sess_stat),
            "since": args.since or "",
            "until": args.until or "",
            "biz_rules_source": args.biz_config or "(内置兜底词典)",
            "theme": args.theme,
        },
    }


def new_agg() -> dict:
    return {
        "biz_input": defaultdict(int), "biz_output": defaultdict(int),
        "biz_cached": defaultdict(int), "biz_reasoning": defaultdict(int),
        "biz_count": defaultdict(int), "biz_models": defaultdict(Counter),
        "biz_sessions": defaultdict(set), "biz_day": defaultdict(Counter),
        "model_input": defaultdict(int), "model_output": defaultdict(int),
        "model_cached": defaultdict(int), "model_reasoning": defaultdict(int),
        "model_count": defaultdict(int),
        "day_total": Counter(),
        "unmatched": 0,
    }


def new_sess_node() -> dict:
    return {"in": 0, "out": 0, "reasoning": 0, "count": 0, "models": Counter(), "first": 0, "last": 0}


def aggregate_to_plain(agg: dict) -> dict:
    return {
        "biz_input": dict(agg["biz_input"]),
        "biz_output": dict(agg["biz_output"]),
        "biz_cached": dict(agg["biz_cached"]),
        "biz_reasoning": dict(agg["biz_reasoning"]),
        "biz_count": dict(agg["biz_count"]),
        "biz_models": {k: dict(v) for k, v in agg["biz_models"].items()},
        "biz_sessions": {k: len(v) for k, v in agg["biz_sessions"].items()},
        "biz_day": {k: dict(v) for k, v in agg["biz_day"].items()},
        "model_input": dict(agg["model_input"]),
        "model_output": dict(agg["model_output"]),
        "model_cached": dict(agg["model_cached"]),
        "model_reasoning": dict(agg["model_reasoning"]),
        "model_count": dict(agg["model_count"]),
        "day_total": dict(agg["day_total"]),
        "unmatched": agg["unmatched"],
    }


def user_query_of(content, limit: int = 200_000) -> str:
    """取出用户真正的发言原文（未清洗），没有则空串。"""
    parts: list[str] = []
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                t = c.get("text") or c.get("content") or ""
                if isinstance(t, str) and t.strip():
                    parts.append(t)
    for p in parts:
        m = USER_QUERY_RE.search(p[:limit])
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def pick_user_intent(content, limit: int = 200_000) -> str:
    """从一条 user 消息里挑出真正由人说的第一段话。

    本机会话日志会把 system prompt / 注入块挂在 role=user 上，真实发言藏在尾部的
    <user_query> 里，所以既不能整条拼接后再判，也不能只看前 N 个字符。

    注意 limit 必须远大于归因用的 --text-limit：注入块动辄上万字符且排在最前面，
    预算太小时用户真正的发言会整个落在窗口之外被丢掉（实测 4000 会全军覆没）。
    """
    q = user_query_of(content, limit)
    if q:
        clean = strip_meta(q)
        if clean:
            return clean
    parts: list[str] = []
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                t = c.get("text") or c.get("content") or ""
                if isinstance(t, str) and t.strip():
                    parts.append(t)
    for p in parts:
        clean = strip_meta(p[:limit])
        if clean and not _looks_like_system(clean[:400]):
            return clean
    return ""


def flatten_text(content, limit: int) -> str:
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        buf = []
        total = 0
        for c in content:
            if isinstance(c, dict):
                t = c.get("text") or c.get("content") or ""
                if isinstance(t, str) and t:
                    buf.append(t)
                    total += len(t)
            if total >= limit:
                break
        return " ".join(buf)[:limit]
    return ""


def collect_audit(args) -> dict:
    events: Counter = Counter()
    files = sorted(glob.glob(os.path.join(args.audit_dir, "*.jsonl")))
    for fp in files:
        name = os.path.basename(fp)
        if args.since and name[:10] < args.since:
            continue
        if args.until and name[:10] > args.until:
            continue
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        d = json.loads(ln)
                    except Exception:
                        continue
                    if isinstance(d, dict):
                        events[d.get("category") or "(未分类)"] += 1
        except OSError:
            continue
    return dict(events)


# ---------------------------------------------------------------- 格式化
def fmt(n) -> str:
    if n is None:
        return "—"
    if n >= 1e8:
        return f"{n / 1e8:.2f} 亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f} 万"
    return f"{int(n):,}"


def fmt_money(x: float) -> str:
    if x >= 10000:
        return f"¥{x / 10000:.2f} 万"
    if x >= 1:
        return f"¥{x:,.2f}"
    return f"¥{x:.4f}"


PALETTE = [
    "#0f766e", "#1d4ed8", "#6d28d9", "#b45309", "#be185d",
    "#4d7c0f", "#dc2626", "#0891b2", "#7c3aed", "#ca8a04",
    "#059669", "#db2777",
]
PALETTE_DARK = [
    "#5eead4", "#7dd3fc", "#c4b5fd", "#fbbf24", "#f472b6",
    "#a3e635", "#fb7185", "#60a5fa", "#facc15", "#34d399",
    "#fda4af", "#94a3b8",
]


def palette(theme: str):
    return PALETTE_DARK if theme == "dark" else PALETTE


CSS = """
:root{
  --bg:#ffffff; --bg2:#f7f8fb; --bg3:#eef1f7; --bd:#dde3ee; --bd2:#c9d2e3;
  --fg:#111827; --fg2:#49536b; --fg3:#79839c;
  --a1:#0f766e; --a2:#1d4ed8; --a3:#6d28d9; --a4:#b45309; --a5:#be185d; --a6:#4d7c0f; --a7:#dc2626; --a8:#0891b2;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
     font-size:13px;line-height:1.6}
.wrap{max-width:1480px;margin:0 auto;padding:26px 24px}
header{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;
       border-bottom:2px solid var(--bd2);padding-bottom:14px;margin-bottom:20px}
header h1{font-size:21px;margin:0 0 4px;letter-spacing:.3px}
header .sub{color:var(--fg3);font-size:11.5px}
.kpi{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:18px}
.kpi .card{background:var(--bg2);border:1px solid var(--bd);border-radius:9px;padding:12px 13px;position:relative;overflow:hidden}
.kpi .card::before{content:'';position:absolute;left:0;top:0;width:3px;height:100%;background:var(--a1)}
.kpi .card:nth-child(2)::before{background:var(--a2)}
.kpi .card:nth-child(3)::before{background:var(--a3)}
.kpi .card:nth-child(4)::before{background:var(--a4)}
.kpi .card:nth-child(5)::before{background:var(--a5)}
.kpi .card:nth-child(6)::before{background:var(--a6)}
.kpi .lbl{color:var(--fg3);font-size:10.5px;letter-spacing:.6px;margin-bottom:5px}
.kpi .val{font-size:21px;font-weight:650;letter-spacing:.2px}
.kpi .sub2{color:var(--fg2);font-size:11px;margin-top:3px}
.row{display:grid;grid-template-columns:1.25fr 1fr;gap:14px;margin-bottom:14px}
.panel{background:var(--bg2);border:1px solid var(--bd);border-radius:9px;padding:15px 16px;margin-bottom:14px}
.panel h2{font-size:14px;margin:0 0 10px;display:flex;align-items:center;justify-content:space-between;gap:8px}
.panel h2 .tag{font-size:10px;color:var(--fg3);font-weight:400;background:var(--bg3);padding:2px 8px;border-radius:8px}
.note{color:var(--fg2);font-size:11px;margin-bottom:9px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--fg3);text-align:left;font-weight:500;padding:6px 8px;border-bottom:1px solid var(--bd2);
   text-transform:none;letter-spacing:.3px;font-size:10.5px;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #e7ebf3}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:var(--bg3)}
.badge{display:inline-block;padding:1px 7px;border-radius:4px;font-size:10px;background:var(--bg3);color:var(--fg2);white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.bar{height:6px;background:var(--bg3);border-radius:3px;overflow:hidden}
.bar>span{display:block;height:100%;border-radius:3px}
.biz-card{border:1px solid var(--bd);background:var(--bg);border-radius:7px;padding:8px 11px;margin-bottom:7px;border-left-width:3px;border-left-style:solid}
.biz-card .nm{display:flex;justify-content:space-between;align-items:center;font-weight:500;font-size:12px;margin-bottom:5px}
.biz-card .meta{display:flex;gap:12px;color:var(--fg2);font-size:11px;margin-top:4px}
.biz-card .meta b{color:var(--fg);font-weight:600}
.insight{display:flex;gap:9px;align-items:flex-start;padding:7px 10px;border-radius:6px;
         background:var(--bg);border:1px solid var(--bd);margin-bottom:6px;font-size:12px;border-left-width:3px;border-left-style:solid}
.insight .ic{font-size:13px;line-height:1.35;flex:none}
.insight.warn{border-left-color:var(--a4)}
.insight.info{border-left-color:var(--a2)}
.insight.hot{border-left-color:var(--a7)}
.insight.ok{border-left-color:var(--a6)}
.foot{text-align:center;color:var(--fg3);font-size:11px;padding:14px 0;border-top:1px solid var(--bd);margin-top:16px}
.trend{display:flex;align-items:flex-end;gap:3px;height:64px;padding-top:6px}
.trend .col{flex:1;background:var(--bg3);border-radius:3px 3px 0 0;position:relative;min-height:2px}
.trend .col:hover{filter:brightness(.92)}
@media (max-width:1100px){.kpi{grid-template-columns:repeat(3,1fr)}.row{grid-template-columns:1fr}}
@media print{
  body{background:#fff}
  .wrap{max-width:none;padding:0}
  .panel,.kpi .card,.biz-card,.insight{background:#fff;border-color:#bbb;break-inside:avoid}
  header{border-bottom:1px solid #999}
}
"""

CSS_DARK = """
:root{
  --bg:#0a0e1a; --bg2:#0f1525; --bg3:#151c33; --bd:#1f2944; --bd2:#2a3555;
  --fg:#e8eaf6; --fg2:#9aa0c6; --fg3:#6b7299;
  --a1:#5eead4; --a2:#7dd3fc; --a3:#c4b5fd; --a4:#fbbf24; --a5:#f472b6; --a6:#a3e635; --a7:#fb7185; --a8:#60a5fa;
}
td{border-bottom:1px dashed #1a2138}
tbody tr:hover td{background:#131a30}
@media print{body{background:#fff;color:#000}.panel,.kpi .card,.biz-card,.insight{background:#fff;border-color:#ccc;color:#000}}
"""


# ---------------------------------------------------------------- 面板渲染
def cost_of(inp: int, out: int, cached: int, reasoning: int, price: dict | None) -> float | None:
    if not price:
        return None
    m = {"cached_in": price.get("cached_input"), "input": price.get("input"),
         "output": price.get("output"), "reasoning": price.get("reasoning") or price.get("output")}
    per_m = 1_000_000.0
    total = 0.0
    if m["input"] is not None:
        billable = max(inp - cached, 0)
        total += billable / per_m * m["input"]
    if m["cached_in"] is not None:
        total += cached / per_m * m["cached_in"]
    elif m["input"] is not None:
        total += cached / per_m * m["input"] * (price.get("cached_discount", 1.0))
    if m["output"] is not None:
        total += max(out - reasoning, 0) / per_m * m["output"]
    if m["reasoning"] is not None:
        total += reasoning / per_m * m["reasoning"]
    return total


def build_price_index(cfg: dict | None) -> dict | None:
    if not cfg:
        return None
    return cfg


def price_for(model: str, cfg: dict | None) -> dict | None:
    if not cfg:
        return None
    low = model.lower()
    for rule in cfg.get("models", []):
        pat = str(rule.get("match", "")).lower()
        if pat and (pat in low or re.search(pat, low)):
            return rule
    d = cfg.get("defaults") or {}
    if d.get("input") is None and d.get("output") is None:
        return None
    return {
        "input": d.get("input"), "output": d.get("output"),
        "cached_input": d.get("cached_input"), "reasoning": d.get("reasoning"),
        "cached_discount": d.get("cached_discount", 0.1),
    }


def render_kpi(a: dict, meta: dict, price_cfg: dict | None) -> str:
    tin = sum(a["biz_input"].values())
    tout = sum(a["biz_output"].values())
    tcached = sum(a["biz_cached"].values())
    treason = sum(a["biz_reasoning"].values())
    matched = sum(a["biz_count"].values())
    recs = matched + a["unmatched"]
    cov = 100 * matched / max(recs, 1)
    cache_rate = 100 * tcached / max(tin, 1)
    days = sorted(a["day_total"])
    money = None
    if price_cfg:
        money = 0.0
        for m in a["model_input"]:
            p = price_for(m, price_cfg)
            if not p:
                continue
            money += cost_of(a["model_input"].get(m, 0), a["model_output"].get(m, 0),
                             a["model_cached"].get(m, 0), a["model_reasoning"].get(m, 0), p) or 0
    return f"""
<div class="kpi">
  <div class="card"><div class="lbl">合计 Token</div><div class="val">{fmt(tin + tout)}</div>
    <div class="sub2">入 {fmt(tin)} + 出 {fmt(tout)}</div></div>
  <div class="card"><div class="lbl">思考模式 token</div><div class="val">{fmt(treason)}</div>
    <div class="sub2">占输出 {100 * treason / max(tout, 1):.1f}%</div></div>
  <div class="card"><div class="lbl">缓存命中率</div><div class="val">{cache_rate:.1f}%</div>
    <div class="sub2">已缓存 {fmt(tcached)}</div></div>
  <div class="card"><div class="lbl">业务归因覆盖</div><div class="val">{cov:.0f}%</div>
    <div class="sub2">{matched}/{recs} 请求已归因</div></div>
  <div class="card"><div class="lbl">{"估算成本" if money is not None else "模型种类"}</div>
    <div class="val">{fmt_money(money) if money is not None else len(a["model_count"])}</div>
    <div class="sub2">{("按 --price-config 估算，仅供内部参考" if money is not None else "请求数 " + fmt(recs))}</div></div>
  <div class="card"><div class="lbl">活跃业务线</div><div class="val">{len(a["biz_count"])}</div>
    <div class="sub2">跨度 {(days[-1] + " ~ " + days[0]) if len(days) > 1 else (days[0] if days else "—")}</div></div>
</div>"""


def render_insight(a: dict, meta: dict, args) -> str:
    items = []
    tot_map = {b: a["biz_input"].get(b, 0) + a["biz_output"].get(b, 0) for b in a["biz_input"]}
    total = sum(tot_map.values()) or 1
    tin = sum(a["biz_input"].values())
    tcached = sum(a["biz_cached"].values())
    tout = sum(a["biz_output"].values())
    treason = sum(a["biz_reasoning"].values())
    recs = sum(a["biz_count"].values()) + a["unmatched"]

    if tot_map:
        srt = sorted(tot_map.items(), key=lambda x: -x[1])
        top_b, top_v = srt[0]
        share = 100 * top_v / total
        if share >= args.concentration_threshold:
            items.append(("hot", f"<b>资源集中度 {share:.0f}%</b>：「{E(top_b)}」消耗 {fmt(top_v)} token，"
                                 f"单条主线吃掉过半预算，其投入产出比应单独复盘"))
        low = ", ".join(E(b) for b, v in sorted(tot_map.items(), key=lambda x: x[1])
                        if v < args.low_input_threshold)
        if low:
            items.append(("warn", f"<b>低投入业务线</b>：{low} — 单线总消耗低于 "
                                  f"{fmt(args.low_input_threshold)} token，建议重估优先级或合并"))

    cov = 100 * sum(a["biz_count"].values()) / max(recs, 1)
    if cov < args.coverage_threshold:
        items.append(("warn", f"<b>归因覆盖率仅 {cov:.0f}%</b>（低于阈值 {args.coverage_threshold:.0f}%）——"
                              f"请补充 <code>--biz-config</code> 词典关键词，否则业务侧结论不可信"))

    cache_rate = 100 * tcached / max(tin, 1)
    if cache_rate >= 90:
        items.append(("info", f"<b>缓存率 {cache_rate:.1f}%</b>：上下文高度复用。"
                              f"若追求降本，精简 system prompt / 收敛长期记忆注入量的边际收益最大"))

    rr = 100 * treason / max(tout, 1)
    if rr >= args.reason_threshold:
        items.append(("info", f"<b>思考 token 占输出 {rr:.1f}%</b>：深度推理密度高，"
                              f"这类任务切到快速模型会导致质量塌陷"))

    flow, deep = [], []
    for b in a["biz_input"]:
        _r = a["biz_reasoning"].get(b, 0) / max(a["biz_output"].get(b, 1), 1)
        if _r < 0.02 and a["biz_output"].get(b, 0) > 5000:
            flow.append(E(b))
        elif _r > 0.25:
            deep.append(E(b))
    if flow:
        items.append(("ok", f"<b>流程搬运型</b>（思考占比 &lt;2%）：{'、'.join(flow[:6])} — 具备切换低价模型的条件"))
    if deep:
        items.append(("info", f"<b>深度调研型</b>（思考占比 &gt;25%）：{'、'.join(deep[:6])} — 慎换模型"))

    if not items:
        items.append(("ok", "各项指标处于阈值内，无显著异常"))

    icon = {"warn": "▲", "info": "●", "hot": "!", "ok": "✓"}
    body = "".join(
        f'<div class="insight {c}"><span class="ic">{icon[c]}</span><div>{t}</div></div>'
        for c, t in items
    )
    return f'<div class="panel"><h2>综合洞察 <span class="tag">阈值可调，见 --help</span></h2>{body}</div>'


def render_biz(a: dict, meta: dict, pal: list[str], top_n: int) -> str:
    tot_in = sum(a["biz_input"].values())
    tot_out = sum(a["biz_output"].values())
    tot_all = tot_in + tot_out or 1
    rows = []
    pairs = sorted(a["biz_input"].items(), key=lambda x: -(x[1] + a["biz_output"].get(x[0], 0)))
    if top_n:
        pairs = pairs[:top_n]
    max_tot = max([a["biz_input"].get(b, 0) + a["biz_output"].get(b, 0) for b, _ in pairs] or [1])
    for i, (b, _) in enumerate(pairs):
        inp = a["biz_input"].get(b, 0)
        out = a["biz_output"].get(b, 0)
        cch = a["biz_cached"].get(b, 0)
        rsn = a["biz_reasoning"].get(b, 0)
        cnt = a["biz_count"].get(b, 0)
        tot = inp + out
        c = pal[i % len(pal)]
        share = 100 * tot / tot_all
        cr = 100 * cch / max(inp, 1)
        rr = 100 * rsn / max(out, 1)
        tag = ""
        if share >= 40:
            tag = f'<span class="badge" style="background:{c}18;color:{c}">集中 {share:.0f}%</span>'
        elif rr >= 25:
            tag = '<span class="badge" style="background:#6d28d918;color:#6d28d9">深度调研</span>'
        elif rr < 2 and out > 5000:
            tag = '<span class="badge" style="background:#4d7c0f18;color:#4d7c0f">流程搬运</span>'
        rows.append(f"""<tr>
<td><span class="dot" style="background:{c}"></span>{E(b)}</td>
<td class="num">{a['biz_sessions'].get(b, 0)}</td>
<td class="num">{cnt}</td>
<td class="num">{fmt(inp)}</td>
<td class="num">{fmt(out)}</td>
<td class="num">{fmt(tot)}</td>
<td class="num">{share:.1f}%</td>
<td class="num">{cr:.1f}%</td>
<td class="num">{fmt(rsn)}</td>
<td><div class="bar"><span style="width:{100 * tot / max_tot:.1f}%;background:{c}"></span></div></td>
</tr>""")
    matched = sum(a["biz_count"].values())
    cov = 100 * matched / max(matched + a["unmatched"], 1)
    return f"""
<div class="panel">
  <h2>业务主线归因 <span class="tag">核心差异点</span></h2>
  <div class="note">归因方法：会话级加权投票（文件名命中为强信号、关键词为弱信号、用户首条意图加成），
  覆盖 {cov:.1f}%，未归因 {a['unmatched']} 条。词典来源：{E(meta['biz_rules_source'])}</div>
  <table><thead><tr>
    <th>业务主线</th><th class="num">会话</th><th class="num">请求</th><th class="num">输入</th>
    <th class="num">输出</th><th class="num">合计</th><th class="num">占比</th>
    <th class="num">缓存率</th><th class="num">思考</th><th>分布</th>
  </tr></thead><tbody>{''.join(rows)}</tbody></table>
</div>"""


def render_reasoning(a: dict, pal: list[str]) -> str:
    tout = sum(a["biz_output"].values())
    treason = sum(a["biz_reasoning"].values())
    rate = 100 * treason / max(tout, 1)
    by = sorted(a["biz_reasoning"].items(), key=lambda x: -x[1])
    cards = []
    rates = [100 * a["biz_reasoning"].get(b, 0) / max(a["biz_output"].get(b, 1), 1) for b, _ in by]
    mx = max(rates or [1])
    for i, (b, r) in enumerate(by):
        out = a["biz_output"].get(b, 0)
        rb = 100 * r / max(out, 1)
        c = pal[i % len(pal)]
        cards.append(f"""<div class="biz-card" style="border-left-color:{c}">
  <div class="nm"><span>{E(b)}</span><span style="color:{c};font-weight:600">{rb:.1f}%</span></div>
  <div class="bar"><span style="width:{100 * rb / max(mx, 1):.1f}%;background:{c}"></span></div>
  <div class="meta"><span>输出 <b>{fmt(out)}</b></span><span>思考 <b>{fmt(r)}</b></span></div>
</div>""")
    return f"""
<div class="panel">
  <h2>思考模式专项 <span class="tag">reasoning_tokens</span></h2>
  <div style="display:flex;gap:22px;margin-bottom:12px">
    <div><div style="color:var(--fg3);font-size:11px">思考 token 总量</div>
      <div style="font-size:19px;font-weight:650">{fmt(treason)}</div></div>
    <div><div style="color:var(--fg3);font-size:11px">占输出比</div>
      <div style="font-size:19px;font-weight:650">{rate:.1f}%</div></div>
  </div>
  {''.join(cards)}
</div>"""


def render_days(a: dict, pal: list[str]) -> str:
    days = sorted(a["day_total"])
    if len(days) < 2:
        return ""
    mx = max(a["day_total"].values()) or 1
    cols = []
    for d in days[-60:]:
        v = a["day_total"][d]
        cols.append(f'<div class="col" title="{d} · {fmt(v)} token" style="height:{max(2, 100 * v / mx):.1f}%;'
                    f'background:{pal[1]}"></div>')
    top_days = sorted(a["day_total"].items(), key=lambda x: -x[1])[:5]
    top_html = " · ".join(f"{d} <b>{fmt(v)}</b>" for d, v in top_days)
    return f"""
<div class="panel">
  <h2>日消耗趋势 <span class="tag">近 {min(60, len(days))} 天</span></h2>
  <div class="trend">{''.join(cols)}</div>
  <div class="note" style="margin-top:8px">峰值日：{top_html}</div>
</div>"""


def render_top_sessions(data: dict, pal: list[str], top_n: int, hide_intent: bool = False) -> str:
    sess = sorted(data["sessions"], key=lambda s: -(s["in"] + s["out"]))[:top_n or 10]
    rows = []
    for i, s in enumerate(sess):
        c = pal[i % len(pal)]
        when = dt.datetime.fromtimestamp(s["last_ts"] / 1000).strftime("%m-%d %H:%M") if s["last_ts"] else "—"
        if hide_intent:
            msg = '<span style="color:var(--fg3)">（已隐藏）</span>'
        else:
            msg = E(s["first_msg"][:52]) if s["first_msg"] else '<span style="color:var(--fg3)">（无记录）</span>'
        rows.append(f"""<tr>
<td class="num" style="color:var(--fg3)">{i + 1}</td>
<td><span class="dot" style="background:{c}"></span>{E(s['biz'])}</td>
<td>{msg}</td>
<td class="num">{fmt(s['in'] + s['out'])}</td>
<td class="num">{s['count']}</td>
<td class="num">{100 * s['reasoning'] / max(s['out'], 1):.0f}%</td>
<td class="num" style="color:var(--fg3)">{when}</td>
</tr>""")
    return f"""
<div class="panel">
  <h2>高消耗会话 Top {len(sess)} <span class="tag">按会话粒度</span></h2>
  <table><thead><tr>
    <th class="num">#</th><th>归主线</th><th>会话首条用户意图</th>
    <th class="num">合计</th><th class="num">请求</th><th class="num">思考占比</th><th class="num">最近活动</th>
  </tr></thead><tbody>{''.join(rows)}</tbody></table>
</div>"""


def render_models(a: dict, pal: list[str], price_cfg: dict | None) -> str:
    row = []
    pairs = sorted(a["model_input"].items(), key=lambda x: -(x[1] + a["model_output"].get(x[0], 0)))
    mx = max([a["model_input"].get(m, 0) + a["model_output"].get(m, 0) for m, _ in pairs] or [1])
    show_cost = bool(price_cfg)
    grand = 0.0
    for i, (m, mi) in enumerate(pairs):
        mo = a["model_output"].get(m, 0)
        mc = a["model_cached"].get(m, 0)
        mr = a["model_reasoning"].get(m, 0)
        cnt = a["model_count"].get(m, 0)
        c = pal[i % len(pal)]
        cost_cell = ""
        if show_cost:
            cost = cost_of(mi, mo, mc, mr, price_for(m, price_cfg))
            grand += cost or 0
            cost_cell = f'<td class="num">{fmt_money(cost) if cost is not None else "—"}</td>'
        row.append(f"""<tr>
<td><span class="dot" style="background:{c}"></span>{E(m)}</td>
<td class="num">{cnt}</td>
<td class="num">{fmt(mi)}</td>
<td class="num">{fmt(mo)}</td>
<td class="num">{100 * mc / max(mi, 1):.1f}%</td>
<td class="num">{fmt(mr)}</td>
<td class="num">{fmt(mi + mo)}</td>{cost_cell}
<td><div class="bar"><span style="width:{100 * (mi + mo) / mx:.1f}%;background:{c}"></span></div></td>
</tr>""")
    cost_th = '<th class="num">估算成本</th>' if show_cost else ""
    foot = (f'<div class="note" style="margin-top:8px">合计估算 <b>{fmt_money(grand)}</b>'
            f'（单价来自 --price-config，为外部输入；脚本不内置任何价格）</div>') if show_cost else \
        ('<div class="note" style="margin-top:8px">未配置 <code>--price-config</code>，'
         '故不输出金额——脚本不内置任何单价，避免给出无法核实的数字。</div>')
    return f"""
<div class="panel">
  <h2>模型分布 <span class="tag">{len(pairs)} 个模型</span></h2>
  <table><thead><tr>
    <th>模型</th><th class="num">请求</th><th class="num">输入</th><th class="num">输出</th>
    <th class="num">缓存率</th><th class="num">思考</th><th class="num">合计</th>{cost_th}<th>分布</th>
  </tr></thead><tbody>{''.join(row)}</tbody></table>
  {foot}
</div>"""


def render_audit(audit: dict, pal: list[str]) -> str:
    if not audit:
        return ""
    total = sum(audit.values()) or 1
    rows = []
    for i, (k, v) in enumerate(sorted(audit.items(), key=lambda x: -x[1])):
        c = pal[i % len(pal)]
        rows.append(f"""<tr>
<td><span class="dot" style="background:{c}"></span>{E(k)}</td>
<td class="num">{v:,}</td>
<td class="num">{100 * v / total:.1f}%</td>
<td><div class="bar"><span style="width:{100 * v / total:.1f}%;background:{c}"></span></div></td>
</tr>""")
    return f"""
<div class="panel">
  <h2>工具调用分布 <span class="tag">audit-log</span></h2>
  <div class="note">数据源：~/.workbuddy/audit-log/*.jsonl，仅计次数，与 token 无直接换算关系</div>
  <table><thead><tr><th>类别</th><th class="num">次数</th><th class="num">占比</th><th>分布</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table>
</div>"""


def render_html(data: dict, args) -> str:
    a, meta, audit = data["agg"], data["meta"], data["audit"]
    pal = palette(args.theme)
    css = CSS + (CSS_DARK if args.theme == "dark" else "")
    title = E(args.title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style></head>
<body><div class="wrap">
<header>
  <div><h1>{title}</h1>
    <div class="sub">数据源：{E(meta['projects_root'])} ＋ {E(meta['audit_dir'] or '（未启用）')}
      · 扫描 {meta['files_scanned']} 个日志文件 / {meta['records']:,} 条有效 usage
      · 会话 {meta['sessions_total']}</div></div>
  <div class="sub" style="text-align:right">生成于 {meta['generated_at']}
    {('· 区间 ' + meta['since'] + ' ~ ' + meta['until']) if meta['since'] or meta['until'] else ''}</div>
</header>

{render_kpi(a, meta, build_price_index(args.price_cfg))}
{render_insight(a, meta, args)}
<div class="row">
  {render_biz(a, meta, pal, args.top)}
  <div>{render_reasoning(a, pal)}{render_audit(audit, pal)}</div>
</div>
{render_days(a, pal)}
{render_models(a, pal, build_price_index(args.price_cfg))}
{render_top_sessions(data, pal, args.top_sessions, args.hide_intent)}

<div class="foot">Token 消耗看板 v{meta['version']} · 单文件离线生成，无外部依赖 ·
  数据全部来自本机会话日志，未上传任何内容</div>
</div></body></html>"""


# ---------------------------------------------------------------- 自检数据
def fake_data() -> dict:
    import random
    random.seed(7)
    biz = ["主线 A", "主线 B", "主线 C", "主线 D"]
    models = ["model-alpha", "model-beta", "model-gamma"]
    now = dt.datetime.now()
    agg = new_agg()
    sessions = []
    for i in range(14):
        b = biz[i % 4]
        m = models[i % 3]
        n = random.randint(3, 40)
        si = so = sr = 0
        for j in range(n):
            inp = random.randint(2000, 90000)
            out = random.randint(300, 6000)
            res = int(out * random.uniform(0, 0.5))
            agg["biz_input"][b] += inp
            agg["biz_output"][b] += out
            agg["biz_cached"][b] += int(inp * 0.9)
            agg["biz_reasoning"][b] += res
            agg["biz_count"][b] += 1
            agg["biz_models"][b][m] += 1
            agg["biz_sessions"][b].add(f"s{i}")
            agg["model_input"][m] += inp
            agg["model_output"][m] += out
            agg["model_cached"][m] += int(inp * 0.9)
            agg["model_reasoning"][m] += res
            agg["model_count"][m] += 1
            day = (now - dt.timedelta(days=random.randint(0, 25))).strftime("%Y-%m-%d")
            agg["day_total"][day] += inp + out
            agg["biz_day"][b][day] += inp + out
            si += inp
            so += out
            sr += res
        sessions.append({
            "sess": f"s{i}", "biz": b, "first_msg": f"自检会话 {i} 的示例首条意图文本",
            "last_ts": int((now - dt.timedelta(days=random.randint(0, 10))).timestamp() * 1000),
            "in": si, "out": so, "reasoning": sr, "count": n, "top_model": m,
        })
    agg["unmatched"] = 12
    return {
        "agg": aggregate_to_plain(agg),
        "sessions": sessions,
        "audit": {"command-safety": 812, "network": 41, "file-safety": 6},
        "meta": {
            "version": VERSION, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "projects_root": "(self-test)", "audit_dir": "(self-test)",
            "files_scanned": 0, "skipped_files": 0, "skipped_lines": 0,
            "records": sum(s["count"] for s in sessions), "sessions_total": len(sessions),
            "since": "", "until": "", "biz_rules_source": "(self-test)", "theme": "light",
        },
    }


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dashboard.py",
        description="从本机 WorkBuddy/CodeBuddy 会话日志生成 Token 消耗看板（单文件离线 HTML）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("-o", "--out", help="输出 HTML 路径")
    p.add_argument("--json-out", help="同时导出结构化 JSON 到此路径")
    p.add_argument("--projects-root", default=DEFAULT_PROJECTS, help="会话日志根目录")
    p.add_argument("--audit-dir", default=DEFAULT_AUDIT, help="审计日志目录")
    p.add_argument("--no-audit", action="store_true", help="跳过审计日志面板")
    p.add_argument("--include-root", action="store_true", help="一并扫描 projects 根目录下的 jsonl")
    p.add_argument("--biz-config", help="业务主线词典 JSON（见 references/biz_rules.example.json）")
    p.add_argument("--price-config", help="模型单价 JSON（见 references/price.example.json；不传则不输出金额）")
    p.add_argument("--since", help="起始日期 YYYY-MM-DD")
    p.add_argument("--until", help="截止日期 YYYY-MM-DD")
    p.add_argument("--theme", choices=["light", "dark"], default="light", help="配色主题，light 可直接打印")
    p.add_argument("--title", default="Token 消耗看板", help="看板标题")
    p.add_argument("--top", type=int, default=0, help="业务主线表显示前 N 条（0=全部）")
    p.add_argument("--top-sessions", type=int, default=10, help="高消耗会话显示条数")
    # 归因权重
    p.add_argument("--filename-weight", type=float, default=1.0, help="文件名命中权重（强信号）")
    p.add_argument("--kw-weight", type=float, default=0.3, help="关键词命中权重（弱信号）")
    p.add_argument("--user-boost", type=float, default=1.8, help="用户消息命中权重倍数")
    p.add_argument("--min-score", type=float, default=0.6, help="会话归因所需最低得分，低于则记未归因")
    p.add_argument("--text-limit", type=int, default=4000, help="单条消息参与归因的文本长度上限")
    # 洞察阈值
    p.add_argument("--concentration-threshold", type=float, default=40.0, help="集中度告警阈值(%%)")
    p.add_argument("--low-input-threshold", type=int, default=30000, help="低投入业务线告警阈值(token)")
    p.add_argument("--coverage-threshold", type=float, default=60.0, help="归因覆盖率告警阈值(%%)")
    p.add_argument("--reason-threshold", type=float, default=20.0, help="思考占比关注阈值(%%)")
    p.add_argument("--self-test", action="store_true", help="用内置假数据跑一遍，验证渲染链路")
    p.add_argument("--hide-intent", action="store_true",
                   help="Top 会话表不显示用户原话（对外分享前务必开启，见 pitfalls.md）")
    p.add_argument("--quiet", action="store_true", help="只在结尾输出一行路径")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    args.price_cfg = None
    if args.price_config:
        try:
            with open(args.price_config, encoding="utf-8") as f:
                args.price_cfg = json.load(f)
        except Exception as e:
            raise SystemExit(f"[x] 单价表读取失败：{e}")

    if args.self_test:
        data = fake_data()
        args.title = args.title if args.title != "Token 消耗看板" else "Token 消耗看板 · 自检样本"
    else:
        biz = load_biz_rules(args.biz_config)
        if not args.quiet:
            print(f"[·] 扫描 {args.projects_root} ...")
        data = collect(args, biz)
        data["meta"]["theme"] = args.theme
        if args.price_cfg:
            pass

    if args.hide_intent:
        for s in data["sessions"]:
            s["first_msg"] = ""

    html = render_html(data, args)
    out = args.out or os.path.join(os.getcwd(), "token-dashboard.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    a = data["agg"]
    tin = sum(a["biz_input"].values())
    tout = sum(a["biz_output"].values())
    treason = sum(a["biz_reasoning"].values())
    matched = sum(a["biz_count"].values())
    recs = matched + a["unmatched"]

    if args.quiet:
        print(out)
        return 0

    print(f"[✓] {out}  ({len(html) / 1024:.0f} KB)")
    print(f"    合计 token     : {fmt(tin + tout)}  (入 {fmt(tin)} / 出 {fmt(tout)})")
    print(f"    思考 token     : {fmt(treason)}  占输出 {100 * treason / max(tout, 1):.1f}%")
    print(f"    缓存命中率     : {100 * sum(a['biz_cached'].values()) / max(tin, 1):.1f}%")
    print(f"    业务主线       : {len(a['biz_count'])} 条，归因覆盖 {100 * matched / max(recs, 1):.1f}%"
          f"（未归因 {a['unmatched']}）")
    if a["biz_input"]:
        top = sorted(a["biz_input"].items(), key=lambda x: -(x[1] + a["biz_output"].get(x[0], 0)))[0]
        print(f"    最大消耗主线   : {top[0]} — {fmt(top[1] + a['biz_output'].get(top[0], 0))}")
    if not args.price_cfg:
        print("    [提示] 未传 --price-config，成本列留空（脚本不内置单价）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
