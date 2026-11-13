# =============================================================================
# send_signpay_reminder.py — Menu 9 Option 4: Send Sign & Pay Reminder Emails
# Scans sign_pay_tracker for clients with incomplete signing or payment,
# sends personalized reminder emails based on docx template.
# =============================================================================

"""
Menu 9 Option 4: Send Sign & Pay Reminder Emails

扫描 sign_pay_tracker，对 Signing Status != Completed 或
Payment Status != Paid 且 Reminder Status != Completed 的客户
批量发送催签字/付款提醒邮件。

架构债 TODO:
  1. 重构时迁移到 _lib/pipelines/signpay_reminder.py
     当前依赖 send_package_email.py 的以下函数：
       load_credentials, send_one, build_message, extract_cid_images,
       scan_taxcycle_pdfs, map_filename, get_signature_docs, find_t1_pdf,
       extract_t1_info, _load_gemini_key, _setup_openai_key, parse_person_names
     send_package_email 重构时需同步更新本文件 import。

  2. 客户主表集成（计划中）：
     - email 优先级改为 主表 > tracker > AI
     - 签字文档映射从主表读，减少每次 PDF 扫描 + AI 调用
     - 主表 schema 确定后重写：
         _get_email_for_client
         _get_signing_docs_for_client
     - 目的：降低 Gemini/OpenAI 调用次数，省额度
"""

import os
import json
import re
from datetime import datetime

from openpyxl import load_workbook

import config

from send_package_email import (
    load_credentials,
    send_one,
    build_message,
    extract_cid_images,
    scan_taxcycle_pdfs,
    map_filename,
    get_signature_docs,
    find_t1_pdf,
    extract_t1_info,
    _load_gemini_key,
    _setup_openai_key,
    parse_person_names,
)

# ── Module constants ────────────────────────────────────────────────────────────

REMINDER_STATUS_SENT = "Sent"
REMINDER_STATUS_COMPLETED = "Completed"

_DISCLAIMER_BANNER = (
    '<div style="background-color: #FFF3CD; '
    'border-left: 4px solid #F0AD4E; '
    'padding: 12px 16px; '
    'margin: 0 0 20px 0; '
    'font-size: 16px; '
    'font-weight: bold; '
    'color: #333333; '
    'font-family: Arial, sans-serif;">'
    'If you have already submitted your signature or completed payment, '
    'please disregard this email.'
    '</div>'
)


# ── Stub functions ──────────────────────────────────────────────────────────────

def _read_signpay_template() -> str:
    """读取 .docx 模板并转成 HTML。返回 HTML 字符串。"""
    import mammoth
    with open(config.SIGNPAY_REMINDER_TEMPLATE, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value


def _identify_blocks(html: str) -> dict:
    """
    在模板 HTML 中定位签字块和付款块的字符偏移。

    返回 dict 包含：
      sign_start, sign_end              签字块整体（含 <ul> 和后续 <p>）
      sign_docs_start, sign_docs_end    签字块内文档列表位置（供替换用）
      sign_note_start, sign_note_end    签字块后的补充说明 <p>
      pay_start, pay_end                付款块整体

    找不到任一 block 时必须 raise ValueError，消息明确：
      "模板格式变化：找不到签字/付款提醒段落，请检查 .docx 模板"
    绝不允许静默跳过或返回 None。
    """
    template_name = os.path.basename(config.SIGNPAY_REMINDER_TEMPLATE)

    # ── 签字块：<ul> 含锚点文本，非贪婪匹配到 </li></ul>
    sign_ul_pat = re.compile(
        r'(<ul><li><strong>We have not yet received your signature</strong>.*?</li></ul>)',
        re.DOTALL,
    )
    sign_ul_m = sign_ul_pat.search(html)
    if not sign_ul_m:
        raise ValueError(
            f"模板格式变化：找不到签字提醒段落，请检查 .docx 模板（{template_name}）"
        )

    # 签字块后紧跟的 <p>（e-signature link 补充说明）
    sign_note_pat = re.compile(
        r'<p>If you did not receive the e-signature link.*?</p>',
        re.DOTALL,
    )
    sign_note_m = sign_note_pat.search(html, sign_ul_m.end())
    if not sign_note_m:
        raise ValueError(
            f"模板格式变化：找不到签字补充说明段落，请检查 .docx 模板（{template_name}）"
        )

    # 签字块内文档列表：在 <strong>...</strong> 之后、</li> 之前
    sign_li_content = sign_ul_m.group(1)
    # 锚点之后的 "on the following forms...):<br />" 到 </li> 之间是文档列表
    docs_pat = re.compile(
        r'on the following forms \(which have been sent to your email via our e-signature platform\):<br />(.*?)</li>',
        re.DOTALL,
    )
    docs_m = docs_pat.search(sign_li_content)
    if not docs_m:
        raise ValueError(
            f"模板格式变化：签字块内未找到文档列表占位，请检查 .docx 模板（{template_name}）"
        )

    # 文档列表的绝对偏移 = 签字 <ul> 起始 + 相对偏移
    sign_ul_start = sign_ul_m.start()
    sign_docs_start = sign_ul_start + docs_m.start(1)
    sign_docs_end = sign_ul_start + docs_m.end(1)

    # ── 付款块：<ul> 含锚点文本
    pay_pat = re.compile(
        r'(<ul><li><strong>We have not yet received your payment\.</strong>.*?</li></ul>)',
        re.DOTALL,
    )
    pay_m = pay_pat.search(html)
    if not pay_m:
        raise ValueError(
            f"模板格式变化：找不到付款提醒段落，请检查 .docx 模板（{template_name}）"
        )

    return {
        "sign_start": sign_ul_m.start(),
        "sign_end": sign_note_m.end(),
        "sign_docs_start": sign_docs_start,
        "sign_docs_end": sign_docs_end,
        "sign_note_start": sign_note_m.start(),
        "sign_note_end": sign_note_m.end(),
        "pay_start": pay_m.start(),
        "pay_end": pay_m.end(),
    }


def _build_email_body(
    template_html: str,
    blocks: dict,
    client_name: str,
    needs_signing: bool,
    needs_payment: bool,
    signing_docs: list,
) -> str:
    """根据 flag 组合裁剪模板、替换客户名和签字文档列表。返回最终 HTML。"""
    if not needs_signing and not needs_payment:
        raise ValueError("needs_signing 和 needs_payment 不能都为 False")

    html = template_html

    # 1. 从后往前裁剪 block（先删 pay 再删 sign，避免偏移错位）
    if not needs_payment:
        html = html[:blocks["pay_start"]] + html[blocks["pay_end"]:]
    if not needs_signing:
        html = html[:blocks["sign_start"]] + html[blocks["sign_end"]:]

    # 2. 替换签字文档列表（必须在 client name 替换之前，使用原始偏移）
    if needs_signing:
        if signing_docs:
            new_docs = "<br />".join(f"o {doc}" for doc in signing_docs)
        else:
            new_docs = "o (see attached documents)"
        # pay block 在 sign block 后面（pay_start > sign_end），删除 pay 不影响 sign 偏移
        html = html[:blocks["sign_docs_start"]] + new_docs + html[blocks["sign_docs_end"]:]

    # 3. 替换 [Client Name]（str.replace 不依赖偏移，安全放在偏移操作之后）
    html = html.replace("[Client Name]", client_name)

    # 4. 替换动态短语（"signed documents and payment" + deadline 句）
    if needs_signing and not needs_payment:
        html = html.replace("signed documents and payment", "signed documents")
        html = html.replace("filing and payment deadline", "filing deadline")
    elif not needs_signing and needs_payment:
        html = html.replace("signed documents and payment", "payment")
        html = html.replace("filing and payment deadline", "payment deadline")

    # 5. 拼接 disclaimer 横幅到最顶部
    return _DISCLAIMER_BANNER + html


def _read_tracker() -> tuple:
    """
    读取 sign_pay_tracker.xlsx。

    过滤规则：
      - 排除 Reminder Status == "Completed"
      - 保留 Signing Status != "Completed" 或 Payment Status != "Paid"
      - Folder Name 和 Client Name 都不为空

    返回 (filtered_rows: list[dict], headers: list[str])
    每个 row dict 包含该行所有列的值（键为 header 名）。
    Reminder Count 空值 → 0；Last Reminder Date 空值 → ""。
    """
    wb = load_workbook(config.SIGN_PAY_TRACKER)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]
    filtered = []
    for row_idx in range(2, ws.max_row + 1):
        row = {}
        for col_idx, hdr in enumerate(headers):
            val = ws.cell(row=row_idx, column=col_idx + 1).value
            row[hdr] = val
        fn = row.get("Folder Name")
        cn = row.get("Client Name")
        if not fn or not cn:
            continue
        if row.get("Reminder Status") == REMINDER_STATUS_COMPLETED:
            continue
        ss = row.get("Signing Status", "")
        ps = row.get("Payment Status", "")
        if ss == "Completed" and ps == "Paid":
            continue
        # 容错：Reminder Count 空值 → 0
        if not row.get("Reminder Count"):
            row["Reminder Count"] = 0
        else:
            try:
                row["Reminder Count"] = int(row["Reminder Count"])
            except (ValueError, TypeError):
                row["Reminder Count"] = 0
        # Last Reminder Date 空值 → ""
        if row.get("Last Reminder Date") is None:
            row["Last Reminder Date"] = ""
        filtered.append(row)

    # 去重：基于 (Folder Name, Client Name)，保留第一次出现
    seen = set()
    deduped = []
    for row in filtered:
        key = (row["Folder Name"], row["Client Name"])
        if key in seen:
            print(f"  [dedup] 跳过重复行: {key[0]} / {key[1]}")
            continue
        seen.add(key)
        deduped.append(row)

    return deduped, headers


def _get_email_for_client(
    folder_name: str,
    client_name: str,
    sent_path: str,
    tracker_email: str,
    gemini_client,
) -> tuple:
    """
    优先级：tracker_email (已存) > AI 从 T1 PDF 抽取。
    返回 (email, source)，source 值域 "tracker" | "ai_extract" | "none"。

    TODO(主表): 将来优先级变为 主表 > tracker > AI。
    主表 schema 确定后在此之前插入:
      master_email = client_master.get_email(client_name)
      if master_email: return (master_email, "master")
    """
    _INVALID_EMAILS = {"unknown", "none", "n/a", ""}

    tracker_val = str(tracker_email).strip() if tracker_email else ""
    if tracker_val and tracker_val.lower() not in _INVALID_EMAILS:
        return (tracker_val, "tracker")

    folder_path = os.path.join(sent_path, folder_name)
    try:
        pdfs = scan_taxcycle_pdfs(folder_path)
        if not pdfs:
            return ("", "none")
        person_names = parse_person_names(folder_name)
        t1_path = find_t1_pdf(
            pdfs,
            person_names[0] if person_names else "",
            all_pdfs=pdfs,
        )
        if not t1_path:
            return ("", "none")
        info = extract_t1_info(gemini_client, t1_path)
        email = (info.get("email", "") or "").strip()
        if email and email.lower() not in _INVALID_EMAILS:
            return (email, "ai_extract")
        return ("", "none")
    except Exception as e:
        print(f"  [WARN] email 抽取失败 {folder_name}: {e}")
        return ("", "none")


def _get_signing_docs_for_client(folder_path: str, client_name: str) -> list:
    """扫描 Taxcycle PDFs -> map_filename -> get_signature_docs。返回显示名列表。"""
    try:
        pdfs = scan_taxcycle_pdfs(folder_path)
        if not pdfs:
            return []
        mapped = [map_filename(os.path.basename(p)) for p in pdfs]
        return get_signature_docs(mapped)
    except Exception as e:
        print(f"  [WARN] 签字文档扫描失败 {folder_path}: {e}")
        return []


def _update_tracker_after_send(
    tracker_path: str,
    folder_name: str,
    client_name: str,
    email: str,
    wb=None,
):
    """
    找到匹配行，更新：
      Email                = email（如果原值为空）
      Reminder Status      = "Sent"
      Reminder Count       = 原值 + 1（空视为 0）
      Last Reminder Date   = 今天（YYYY-MM-DD）

    可接受已打开的 wb，返回 wb 对象。本函数不调用 wb.save()。
    """
    from datetime import date

    if wb is None:
        wb = load_workbook(tracker_path)
    ws = wb.active
    headers = [cell.value for cell in ws[1]]

    col_idx = {}
    for name in ["Folder Name", "Client Name", "Email",
                 "Reminder Status", "Reminder Count", "Last Reminder Date"]:
        col_idx[name] = (headers.index(name) + 1) if name in headers else None

    target_row = None
    for row_idx in range(2, ws.max_row + 1):
        fn = ws.cell(row=row_idx, column=col_idx["Folder Name"]).value
        cn = ws.cell(row=row_idx, column=col_idx["Client Name"]).value
        if fn == folder_name and cn == client_name:
            target_row = row_idx
            break

    if target_row is None:
        print(f"  [WARN] tracker 找不到行: {folder_name} / {client_name}")
        return wb

    today_str = date.today().isoformat()

    if col_idx["Email"]:
        cur_email = ws.cell(row=target_row, column=col_idx["Email"]).value
        if not cur_email and email:
            ws.cell(row=target_row, column=col_idx["Email"]).value = email

    if col_idx["Reminder Status"]:
        ws.cell(row=target_row, column=col_idx["Reminder Status"]).value = "Sent"

    if col_idx["Reminder Count"]:
        cur_count = ws.cell(row=target_row, column=col_idx["Reminder Count"]).value
        try:
            cur_count = int(cur_count) if cur_count else 0
        except (ValueError, TypeError):
            cur_count = 0
        ws.cell(row=target_row, column=col_idx["Reminder Count"]).value = cur_count + 1

    if col_idx["Last Reminder Date"]:
        ws.cell(row=target_row, column=col_idx["Last Reminder Date"]).value = today_str

    return wb


def _write_log(results: list) -> None:
    """
    追加到 SIGNPAY_REMINDER_LOG。

    每条记录 schema:
      {
        "timestamp": ISO8601,
        "folder_name": str,
        "client_name": str,
        "email": str,
        "email_source": "tracker" | "ai_extract" | "none",
        "needs_signing": bool,
        "needs_payment": bool,
        "status": "sent" | "failed" | "skipped",
        "error": str | null
      }

    email_source 字段说明：记录本次 email 来源，将来统计 AI 调用频率
    评估客户主表 ROI 用。
    """
    log_path = config.SIGNPAY_REMINDER_LOG
    if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
        try:
            with open(log_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            corrupt_path = log_path + ".corrupt"
            os.replace(log_path, corrupt_path)
            print(f"  [WARN] 日志文件损坏，已备份到 {corrupt_path}")
            data = []
    else:
        data = []

    data.extend(results)

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_signpay_reminder() -> None:
    """Menu 9 Option 4 入口：批量发送签字/付款提醒邮件。"""
    from datetime import datetime

    # [1] 启动提示
    print("=" * 60)
    print("  Menu 9 Option 4: Send Sign & Pay Reminder Emails")
    print("=" * 60)
    print()
    print("提示：如需重新抽取某客户邮箱，请在 tracker 中清空该行")
    print("     Email 列后重跑。")
    print()

    # [2] 路径/凭证预检
    tracker_path = config.SIGN_PAY_TRACKER
    if not os.path.exists(tracker_path):
        print(f"  [ERROR] Tracker 不存在: {tracker_path}")
        return
    if not os.path.exists(config.SIGNPAY_REMINDER_TEMPLATE):
        print(f"  [ERROR] 模板不存在: {config.SIGNPAY_REMINDER_TEMPLATE}")
        return
    creds = load_credentials()
    if not creds:
        print("  [ERROR] 无法加载邮件凭证")
        return

    # [3] 加载一次性资源
    template_html = _read_signpay_template()
    blocks = _identify_blocks(template_html)

    _setup_openai_key()
    from google import genai
    gemini_api_key = _load_gemini_key()
    gemini_client = genai.Client(api_key=gemini_api_key)

    # [4] 读候选
    rows, headers = _read_tracker()
    if not rows:
        print("  没有需要提醒的客户。")
        return

    # [5] 构建候选详情
    candidates = []
    for row in rows:
        folder_name = row["Folder Name"]
        client_name = row["Client Name"]
        folder_path = os.path.join(config.SENT_PATH, folder_name)

        needs_signing = row.get("Signing Status") != "Completed"
        needs_payment = row.get("Payment Status") != "Paid"
        if not (needs_signing or needs_payment):
            continue

        email, email_source = _get_email_for_client(
            folder_name, client_name, config.SENT_PATH,
            row.get("Email", ""), gemini_client,
        )

        signing_docs = []
        if needs_signing:
            signing_docs = _get_signing_docs_for_client(folder_path, client_name)

        candidates.append({
            "folder_name": folder_name,
            "client_name": client_name,
            "email": email,
            "email_source": email_source,
            "needs_signing": needs_signing,
            "needs_payment": needs_payment,
            "signing_docs": signing_docs,
            "folder_path": folder_path,
        })

    if not candidates:
        print("  没有需要提醒的客户。")
        return

    # [6] 显示候选摘要表
    total = len(candidates)
    both_count = sum(1 for c in candidates if c["needs_signing"] and c["needs_payment"])
    sign_only = sum(1 for c in candidates if c["needs_signing"] and not c["needs_payment"])
    pay_only = sum(1 for c in candidates if not c["needs_signing"] and c["needs_payment"])
    no_email = sum(1 for c in candidates if not c["email"])

    print(f"候选 {total} 人（签字+付款 {both_count}，仅签字 {sign_only}，"
          f"仅付款 {pay_only}）")
    if no_email:
        print(f"  其中 {no_email} 人无法获取邮箱，将跳过发送")
    print()
    print(f"{'#':>3}  {'Folder':<30}  {'Client':<20}  {'Email':<35}  "
          f"{'Sign':<5}  {'Pay':<5}  {'Docs':<15}  {'Src':<10}")
    print("-" * 140)
    for i, c in enumerate(candidates, 1):
        email_show = c["email"] if c["email"] else "(no email)"
        docs_show = ",".join(c["signing_docs"]) if c["signing_docs"] else "-"
        sign_mark = "YES" if c["needs_signing"] else "-"
        pay_mark = "YES" if c["needs_payment"] else "-"
        print(f"{i:>3}  {c['folder_name'][:30]:<30}  {c['client_name'][:20]:<20}  "
              f"{email_show[:35]:<35}  {sign_mark:<5}  {pay_mark:<5}  "
              f"{docs_show[:15]:<15}  {c['email_source']:<10}")
    print()

    # [7] 三选一确认
    while True:
        choice = input(
            "  [A] 全部发送   [S] 逐个确认   [N] 取消   选择: "
        ).strip().upper()
        if choice in ("A", "S", "N"):
            break
        print("  无效选择")

    if choice == "N":
        print("  已取消")
        return

    # [8] 执行发送
    wb = load_workbook(tracker_path)
    results = []
    sent_count = 0
    failed_count = 0
    skipped_count = 0

    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}/{len(candidates)}] {c['folder_name']} / {c['client_name']}")

        if choice == "S":
            sub = input("    发送？ [Y] 是  [N] 跳过  [Q] 退出: ").strip().upper()
            if sub == "Q":
                print("  用户中断，保存已发送记录")
                break
            if sub != "Y":
                print("    跳过")
                skipped_count += 1
                results.append({
                    "timestamp": datetime.now().isoformat(),
                    "folder_name": c["folder_name"],
                    "client_name": c["client_name"],
                    "email": c["email"],
                    "email_source": c["email_source"],
                    "needs_signing": c["needs_signing"],
                    "needs_payment": c["needs_payment"],
                    "status": "skipped",
                    "error": "user_skip",
                })
                continue

        if not c["email"]:
            print("    [SKIP] 无邮箱")
            skipped_count += 1
            results.append({
                "timestamp": datetime.now().isoformat(),
                "folder_name": c["folder_name"],
                "client_name": c["client_name"],
                "email": "",
                "email_source": c["email_source"],
                "needs_signing": c["needs_signing"],
                "needs_payment": c["needs_payment"],
                "status": "skipped",
                "error": "no_email",
            })
            continue

        try:
            body_html = _build_email_body(
                template_html, blocks,
                c["client_name"],
                c["needs_signing"], c["needs_payment"],
                c["signing_docs"],
            )
            body_html_final, inline_images = extract_cid_images(body_html)
            subject = config.SIGNPAY_REMINDER_SUBJECT.format(
                client_name=c["client_name"],
            )
            msg = build_message(
                person_name=c["client_name"],
                recipient=c["email"],
                body=body_html_final,
                inline_images=inline_images,
                is_html=True,
                attachments=[],
                creds=creds,
                cc="",
                subject_override=subject,
            )
            success, err = send_one(msg, c["email"], creds)
            if not success:
                raise RuntimeError(f"send_one 失败: {err}")

            wb = _update_tracker_after_send(
                tracker_path, c["folder_name"], c["client_name"],
                c["email"], wb=wb,
            )
            wb.save(tracker_path)

            print(f"    [OK] 发送至 {c['email']}")
            sent_count += 1
            results.append({
                "timestamp": datetime.now().isoformat(),
                "folder_name": c["folder_name"],
                "client_name": c["client_name"],
                "email": c["email"],
                "email_source": c["email_source"],
                "needs_signing": c["needs_signing"],
                "needs_payment": c["needs_payment"],
                "status": "sent",
                "error": None,
            })

        except Exception as e:
            print(f"    [FAIL] {e}")
            failed_count += 1
            results.append({
                "timestamp": datetime.now().isoformat(),
                "folder_name": c["folder_name"],
                "client_name": c["client_name"],
                "email": c["email"],
                "email_source": c["email_source"],
                "needs_signing": c["needs_signing"],
                "needs_payment": c["needs_payment"],
                "status": "failed",
                "error": str(e),
            })
            continue

    _write_log(results)
    print()
    print("=" * 60)
    print(f"  完成：发送 {sent_count}，跳过 {skipped_count}，失败 {failed_count}")
    print(f"  日志：{config.SIGNPAY_REMINDER_LOG}")
    print("=" * 60)
