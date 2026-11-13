# T2 Reminder 模板对接规范

## 模板文件清单

| 文件名 | 语言 |
|---|---|
| `T2 Reminder - R1 - ENG.docx` | 英文 |
| `T2 Reminder - R2 - ENG.docx` | 英文 |
| `T2 Reminder - R3 - ENG.docx` | 英文 |
| `T2 Reminder - R1 - CHN.docx` | 简体中文 |
| `T2 Reminder - R2 - CHN.docx` | 简体中文 |
| `T2 Reminder - R3 - CHN.docx` | 简体中文 |
| `T2 Reminder - R1 - VIE.docx` | 越南文 |
| `T2 Reminder - R2 - VIE.docx` | 越南文 |
| `T2 Reminder - R3 - VIE.docx` | 越南文 |

存放路径：`ReminderTemplet\`

---

## 占位符

| 占位符 | 替换内容 | 示例 |
|---|---|---|
| `{NAME}` | 客户联系人姓名 | `John Smith` / `张伟` |
| `{COMPANY}` | 公司法定名称 | `ABC Holdings Ltd.` |
| `{MONTH}` | T2 YE 所属月份 | ENG: `February 2026` / CHN: `2026年2月` / VIE: `Tháng 2/2026` |

> 占位符区分大小写，必须使用花括号 `{}`，拼写不可更改。

---

## 邮件主题行

文档**第一行**须以 `Subject:` 开头，系统自动提取为邮件主题（不显示在正文中）：

**ENG：**
```
Subject: T2 Filing Reminder – {MONTH} – {COMPANY}
```

**CHN：**
```
Subject: T2报税提醒 – {MONTH} – {COMPANY}
```

**VIE：**
```
Subject: Nhắc nhở nộp T2 – {MONTH} – {COMPANY}
```

---

## 格式支持

系统可保留以下 Word 格式并渲染至邮件：
- 字体颜色、高亮、加粗、斜体、下划线
- 段落对齐
- 内嵌图片（CID inline，邮件客户端正常显示）
- 表格

---

## 落款

系统自动追加，**模板中无需写落款**：

| 语言 | 自动追加内容 |
|---|---|
| ENG | Best regards, **ExampleCo Group** / Example CPA Firm P.C. Inc. / infosk@example-firm.com |
| CHN | 谢谢！**ExampleCo Group** / Example CPA Firm P.C. Inc. / infosk@example-firm.com |
| VIE | Trân trọng, **ExampleCo Group** / Example CPA Firm P.C. Inc. / infosk@example-firm.com |

---

## 发送逻辑（供参考）

| Round | 触发条件（当前月 − T2 YE 月） |
|---|---|
| R1 | elapsed = 1（YE 后第 1 个月） |
| R2 | elapsed = 2（YE 后第 2 个月） |
| R3 | elapsed = 3（YE 后第 3 个月） |

超出 3 个月窗口后不再发送。同一轮次当月已发则跳过。
