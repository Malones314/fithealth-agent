"""离线查看 agent trace（agent-trace 阶段 6）。

## 为什么是 CLI，而不是一个 `/debug/traces` 路由

trace 里有健康信号与用户消息摘要。多一个 HTTP 入口就多一份暴露面，而本项目还没有
登录态（README「网络暴露边界」一节）。CLI 只在开发者的机器上跑，读的是本地文件，
不需要认证、不需要路径遍历防护、也不用动被冻结的路由快照。

## 三条硬约束

1. **只读**。本脚本不写、不删、不移动任何 trace 文件；唯一的写动作是
   `--html` 指定的报告，而它**不允许落在 trace 目录里**，也不覆盖已有文件
   （除非显式 `--force`）。
2. **输入不接受路径**。`--turn` 只收回合 id 并按正则校验；目录一律从
   `FITHEALTH_TRACE_DIR` / `FITHEALTH_DATA_DIR` 解析。没有"读哪个文件都行"的入口。
3. **输出全部转义**。TRACE-08 的教训：框架那套 HTML 把 payload 直接插进
   `<pre>{...}</pre>`，而 payload 里是**原始用户输入**——一条含
   `</pre><script>` 的消息会在审计者打开报告时执行。这里每个动态值都过
   `html.escape(quote=True)`，控制字符换成可见记号，并且报告本身不引用任何外部资源
   （附 `default-src 'none'` 的 CSP）。

## 用法

```bash
/e/anaconda3/python scripts/trace_report.py --list --last 20
/e/anaconda3/python scripts/trace_report.py --turn t-20260904-101929-74ca3f
/e/anaconda3/python scripts/trace_report.py --turn t-20260904-101929-74ca3f --html out.html
/e/anaconda3/python scripts/trace_report.py --failed-plans --since 7d
```
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fithealth_agent.observability import INDEX_NAME, TURN_PREFIX, trace_dir  # noqa: E402


#: 回合 id 的形状（`trace.new_turn_id`），撞名重试会多一个 `-<n>` 后缀。
#: 用它做**白名单**校验：`--turn` 于是不可能变成一条路径。
TURN_ID = re.compile(r"^t-\d{8}-\d{6}-[0-9a-f]{6}(?:-\d+)?$")

#: `--since` 只认"整数 + d/h"。含糊的时间表达式在排障脚本里只会带来误判。
SINCE = re.compile(r"^(\d+)([dh])$")

#: 要替换掉的码位区间。写成十六进制数字而不是字面量：这些字符本身不可见，直接写进
#: 源码会让文件在别的编辑器里被"顺手修好"，而修好之后这道防线就没了。
#:
#: 它们不是 XSS，但足以让报告在浏览器或终端里显示成另一段内容——BiDi 覆写尤其能把
#: 一行文本整段反转过来显示。
_UNSAFE_RANGES = (
    (0x00, 0x08),      # C0，留下 \t(0x09) 与 \n(0x0A)
    (0x0B, 0x1F),      # 其余 C0
    (0x7F, 0x9F),      # DEL 与 C1
    (0x2028, 0x2029),  # 行分隔符、段分隔符
    (0x202A, 0x202E),  # BiDi 覆写
    (0x2066, 0x2069),  # BiDi 隔离
)
_UNSAFE = re.compile(
    "[" + "".join(f"{chr(low)}-{chr(high)}" for low, high in _UNSAFE_RANGES) + "]"
)


def _escape_control(match: re.Match[str]) -> str:
    """换成常规的 `\\xNN` / `\\uXXXX` 记号：既看得见，又不可能改变结构。"""
    code = ord(match.group())
    return f"\\x{code:02X}" if code < 0x100 else f"\\u{code:04X}"


def _safe(value: object) -> str:
    """任何值 → 可以安全插进 HTML 文本节点**或属性**的字符串。

    两步都必需：先把控制字符换成可见记号（它们没法靠转义变安全，只能替换），再交给
    `html.escape(quote=True)`——`quote=True` 覆盖属性上下文，少了它 `title="…"`
    这类位置就能被一个引号闭合掉。
    """
    text = value if isinstance(value, str) else str(value)
    return html.escape(_UNSAFE.sub(_escape_control, text), quote=True)


def read_index(directory: Path) -> list[dict]:
    """读 index.jsonl，最新在后。坏行跳过（stream 模式崩溃可能留半行）。"""
    return _read_jsonl(directory / INDEX_NAME)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    # errors="replace"：报告工具遇到坏字节应当照样出报告，而不是自己崩掉。
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def turn_path(directory: Path, turn_id: str) -> Path | None:
    """按回合 id 定位文件。**不接受路径**：id 已经过 `TURN_ID` 白名单校验。"""
    for row in reversed(read_index(directory)):
        if row.get("turn_id") == turn_id and isinstance(row.get("file"), str):
            candidate = directory / str(row.get("day") or "") / row["file"]
            if candidate.is_file():
                return candidate
    # index 丢过行（追加失败只降级为告警）时回落到按天扫。
    matches = sorted(directory.glob(f"*/{TURN_PREFIX}{turn_id}.jsonl"))
    return matches[-1] if matches else None


def read_events(path: Path) -> list[dict]:
    """一个回合的事件，按 `seq` 排序——顺序只由它承载，不看 `ts`。"""
    events = _read_jsonl(path)
    events.sort(key=lambda item: item.get("seq") or 0)
    return events


def _since_cutoff(raw: str) -> datetime:
    match = SINCE.match(raw)
    if match is None:
        raise SystemExit(f"--since 只接受 <整数>d 或 <整数>h，收到 {raw!r}")
    amount, unit = int(match.group(1)), match.group(2)
    delta = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
    return datetime.now().astimezone() - delta


def _started_at(row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row.get("started_at")))
    except (TypeError, ValueError):
        return None


# ── 三个子命令 ──────────────────────────────────────────────────────────


def list_turns(directory: Path, *, last: int, since: str | None) -> int:
    rows = read_index(directory)
    if since:
        cutoff = _since_cutoff(since)
        rows = [row for row in rows if (_started_at(row) or cutoff) >= cutoff]
    if not rows:
        print(f"{directory} 下没有回合记录")
        return 0
    print(f"{'turn_id':<28} {'started_at':<25} {'route':<16} {'status':<8} {'events':>6} detail")
    for row in rows[-last:]:
        print(
            f"{str(row.get('turn_id')):<28} {str(row.get('started_at')):<25} "
            f"{str(row.get('route')):<16} {str(row.get('status')):<8} "
            f"{str(row.get('events')):>6} {row.get('detail_level')}"
        )
    print(f"\n共 {len(rows)} 个回合，显示最近 {min(last, len(rows))} 个")
    return 0


def _event_line(event: dict) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    parts = [f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in payload.items()]
    step = f" step={event.get('step')}" if event.get("step") is not None else ""
    duration = f" dur_ms={event.get('dur_ms')}" if event.get("dur_ms") is not None else ""
    return (
        f"{event.get('seq'):>3} {str(event.get('kind')):<14} "
        f"{str(event.get('span')):<24}{step}{duration} " + " ".join(parts)
    )


def show_turn(directory: Path, turn_id: str, *, html_path: Path | None, force: bool) -> int:
    if not TURN_ID.match(turn_id):
        raise SystemExit(f"回合 id 形状不对：{turn_id!r}（应形如 t-20260904-101929-74ca3f）")
    path = turn_path(directory, turn_id)
    if path is None:
        raise SystemExit(f"{directory} 下找不到回合 {turn_id}")
    events = read_events(path)
    for event in events:
        print(_event_line(event))
    print(f"\n共 {len(events)} 个事件，来自 {path}")
    if html_path is not None:
        target = _checked_output(html_path, directory, force=force)
        _write_html(target, turn_id, path, events, force=force)
        print(f"HTML 报告已写入 {target}")
    return 0


def failed_plans(directory: Path, *, since: str | None) -> int:
    """列出计划校验或自动修正失败的回合。

    这是"为什么它没给我生成训练计划"最常用的入口（验收问题 #1）。判据取自
    `gate/plan_validation` 与 `gate/auto_correction` 的 `outcome`，不看回复正文。
    """
    cutoff = _since_cutoff(since) if since else None
    hits = 0
    for row in read_index(directory):
        started = _started_at(row)
        if cutoff is not None and started is not None and started < cutoff:
            continue
        name, day = row.get("file"), row.get("day")
        if not isinstance(name, str) or not isinstance(day, str):
            continue
        path = directory / day / name
        if not path.is_file():
            continue
        reasons = [
            f"{event.get('span')}={event['payload'].get('outcome')}"
            for event in read_events(path)
            if event.get("kind") == "gate"
            and event.get("span") in {"plan_validation", "auto_correction"}
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("outcome") == "failed"
        ]
        if reasons:
            hits += 1
            print(f"{row.get('turn_id')}  {row.get('started_at')}  " + "  ".join(reasons))
    print(f"\n共 {hits} 个回合的计划校验或自动修正失败")
    return 0


# ── HTML 报告 ───────────────────────────────────────────────────────────

#: 报告的头部。**没有任何外部引用**：样式内联，没有 script、img、link。
#: CSP 是纵深防御——万一以后有人往模板里加了一个 URL，`default-src 'none'` 会挡住它。
_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>{title}</title>
<style>
body {{ font: 13px/1.5 Consolas, Menlo, monospace; margin: 2rem; color: #222; }}
h1 {{ font-size: 1.1rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 4px 8px; text-align: left; vertical-align: top; }}
th {{ background: #f4f4f4; }}
pre {{ margin: 0; white-space: pre-wrap; word-break: break-all; }}
.meta {{ color: #666; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="meta">{meta}</p>
<table>
<tr><th>seq</th><th>kind</th><th>span</th><th>step</th><th>dur_ms</th><th>payload</th></tr>
"""

_HTML_TAIL = """</table>
</body>
</html>
"""


def render_html(turn_id: str, source: Path, events: list[dict]) -> str:
    """渲染报告。**每个**动态值都过 `_safe`——这是 TRACE-08 不复发的唯一保证。"""
    meta = f"来源 {source.name}｜{len(events)} 个事件｜生成于 {datetime.now().isoformat(timespec='seconds')}"
    rows = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        rows.append(
            "<tr>"
            f"<td>{_safe(event.get('seq'))}</td>"
            f"<td>{_safe(event.get('kind'))}</td>"
            f"<td>{_safe(event.get('span'))}</td>"
            f"<td>{_safe('' if event.get('step') is None else event.get('step'))}</td>"
            f"<td>{_safe('' if event.get('dur_ms') is None else event.get('dur_ms'))}</td>"
            f"<td><pre>{_safe(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></td>"
            "</tr>\n"
        )
    return (
        _HTML_HEAD.format(title=_safe(f"trace {turn_id}"), meta=_safe(meta))
        + "".join(rows)
        + _HTML_TAIL
    )


def _checked_output(target: Path, directory: Path, *, force: bool) -> Path:
    """报告的落点必须安全：不在 trace 目录里，不静默覆盖已有文件。"""
    resolved = target.expanduser().resolve()
    if resolved == directory.resolve() or directory.resolve() in resolved.parents:
        raise SystemExit(f"拒绝把报告写进 trace 目录：{resolved}")
    if resolved.exists() and not force:
        raise SystemExit(f"{resolved} 已存在；要覆盖请显式加 --force")
    return resolved


def _write_html(target: Path, turn_id: str, source: Path, events: list[dict], *, force: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # "x" 是无覆盖创建：即使 `_checked_output` 与这里之间有人抢先建了文件也不会被盖掉。
    with target.open("w" if force else "x", encoding="utf-8", newline="\n") as handle:
        handle.write(render_html(turn_id, source, events))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线查看 agent trace（只读）")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="列出回合")
    action.add_argument("--turn", metavar="TURN_ID", help="展开一个回合的事件")
    action.add_argument("--failed-plans", action="store_true", help="列出计划校验失败的回合")
    parser.add_argument("--last", type=int, default=20, help="--list 显示最近几个（默认 20）")
    parser.add_argument("--since", default=None, help="只看这段时间内的回合，如 7d / 12h")
    parser.add_argument("--html", type=Path, default=None, help="把 --turn 的结果渲染成 HTML")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的 --html 文件")
    args = parser.parse_args(argv)

    directory = trace_dir()
    if not directory.is_dir():
        print(f"trace 目录不存在：{directory}（FITHEALTH_TRACE_DIR / FITHEALTH_DATA_DIR）")
        return 0
    if args.last <= 0:
        raise SystemExit("--last 必须大于 0")
    if args.html is not None and not args.turn:
        raise SystemExit("--html 只配合 --turn 使用")

    if args.turn:
        return show_turn(directory, args.turn, html_path=args.html, force=args.force)
    if args.failed_plans:
        return failed_plans(directory, since=args.since)
    return list_turns(directory, last=args.last, since=args.since)


if __name__ == "__main__":
    raise SystemExit(main())
