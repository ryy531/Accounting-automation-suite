#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_todo_extract.py — pulls ad-hoc to-do items out of the WhatsApp chat
history (Alex's voice memos, staff requests, anything not already tracked
as a NocoDB engagement) for a given date range and writes a structured JSON
list to OUT_FILE.

Deploy: copy this file to D:\\AI_Bot\\openclaw\\data\\config\\weekly_todo_extract.py
(same directory as nocodb_briefing.py / email_briefing.py — it becomes
/home/node/.openclaw/weekly_todo_extract.py inside the container via the
existing bind mount).

Invoked by the host-side WeeklyTodoReport/runners/weekly_report.py via
`docker exec`:
    docker exec openclaw-openclaw-gateway-1 python3 \
        /home/node/.openclaw/weekly_todo_extract.py 2026-06-22 2026-06-26

Why scan ALL session files instead of following one session id: OpenClaw
resets (/new, /reset) start a new session id and stash the old file's tail
as "<id>.jsonl.reset.<timestamp>", so a single logical WhatsApp thread is
spread across many files over a week with no field linking the backup back
to its original session key. Matching on timestamp range across every file
in the sessions directory is robust to that churn and avoids re-deriving
the reset chain each run.

Data handling note: this sends the week's WhatsApp transcript text to the
DeepSeek API (same external call email_briefing.py already makes for email
summaries) to extract a to-do list. It does not store transcripts anywhere
beyond OUT_FILE, which holds only the extracted structured to-do items.
"""
import glob
import json
import os
import sys
import urllib.request

SESSIONS_DIR = "/home/node/.openclaw/agents/main/sessions"
OUT_FILE = "/home/node/.openclaw/weekly_todo_extract.json"
LOG_FILE = "/home/node/.openclaw/weekly_todo_extract.log"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-pro"

PROMPT = """你是 ExampleCo 会计事务所的助理。下面是本周（{start} 至 {end}）WhatsApp 群聊/私聊的完整聊天记录（含系统消息噪音，请忽略网关连接/断开等系统日志）。

请从中提取出【还需要人去跟进处理、且尚未在 NocoDB 系统里被结构化记录的临时性代办事项】，重点关注：
- Alex、Jamie、Morgan、Sam、Riley 等人提出的、需要后续跟进的口头/语音交代事项
- 需要澄清但还没澄清的事情（比如提到了人名/项目但缺上下文）
- 客户跟进、邮件待处理、内部协调事项
- 明确已经完成/已解决的事情不要包含
- 纯粹的状态查询对话、NocoDB engagement 状态更新确认（比如"xxx payroll做完了"→已更新）不要包含，那些已经在 NocoDB 里了
- 系统连接状态、模型切换等技术日志不要包含

按时间顺序输出一个 JSON 数组，每项格式：
{{"date": "MM-DD", "category": "客户跟进|内部协调|邮件处理|待澄清|其他", "item": "一句话描述事项", "note": "补充背景，比如是谁提出的、还差什么信息"}}

只输出 JSON 数组本身，不要任何其他文字、不要 markdown 代码块标记。如果没有任何需要跟进的事项，输出 []

聊天记录：
"""


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def collect_messages(start, end):
    out = []
    seen = set()
    for path in glob.glob(os.path.join(SESSIONS_DIR, "*.jsonl*")):
        if path.endswith("sessions.json"):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    ts = obj.get("timestamp", "")
                    if not (start <= ts[:10] <= end):
                        continue
                    msg = obj.get("message", {})
                    role = msg.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    content = msg.get("content")
                    text = ""
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "text":
                                text += c.get("text", "") + " "
                    elif isinstance(content, str):
                        text = content
                    text = text.strip()
                    if not text:
                        continue
                    key = (ts, role, text[:60])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((ts, role, text))
        except Exception as exc:
            log(f"skip {path}: {exc}")
    out.sort(key=lambda r: r[0])
    return out


def main():
    if len(sys.argv) < 3:
        print("usage: weekly_todo_extract.py START_DATE END_DATE", file=sys.stderr)
        sys.exit(1)
    start, end = sys.argv[1], sys.argv[2]

    messages = collect_messages(start, end)
    log(f"{start}..{end}: collected {len(messages)} messages")

    if not messages:
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return

    transcript = "\n".join(f"[{ts} {role}] {text}" for ts, role, text in messages)
    MAX_CHARS = 60000
    if len(transcript) > MAX_CHARS:
        transcript = transcript[-MAX_CHARS:]

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        log("DEEPSEEK_API_KEY missing")
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump({"error": "DEEPSEEK_API_KEY missing"}, f)
        sys.exit(1)

    prompt = PROMPT.format(start=start, end=end) + transcript
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = json.load(r)
        raw = resp["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log(f"DeepSeek call failed: {exc}")
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f)
        sys.exit(1)

    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        items = json.loads(raw)
    except Exception as exc:
        log(f"JSON parse failed: {exc} -- raw: {raw[:300]}")
        items = {"error": "parse_failed", "raw": raw}

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    log(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
