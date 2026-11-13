# Annual Return Reminder 模板对接规范

## 模板文件清单

| 文件名 | 语言 |
|---|---|
| `Annual Return Reminder - CN.docx` | 简体中文 |
| `Annual Return Reminder - EN.docx` | 英文 |

存放路径：`AnnualReturn\Templet\`

---

## 占位符

| 占位符 | 替换内容 | 示例 |
|---|---|---|
| `{NAME}` | 客户联系人姓名 | `John Smith` / `张伟` |
| `{COMPANY}` | 公司法定名称 | `ABC Holdings Ltd.` |
| `{CORP_NO}` | 公司注册编号 | `BC1234567` |
| `{DUE_DATE}` | 到期日 | EN: `April 15, 2026` / CN: `2026-04-15` |
| `{PROVINCE}` | 注册省份 | `BC` / `Federal` |
| `{REGISTRY}` | 注册局名称（按省自动选择） | 见下表 |
| `{INFO_URL}` | 客户信息确认表链接 | `https://example-firm.com/annual-return/` |

### `{REGISTRY}` 对应关系

| Province | 替换值 |
|---|---|
| Federal | Corporations Canada |
| BC | BC Registry |
| AB | Alberta Corporate Registry |
| SK | Saskatchewan Corporate Registry |
| ON | Ontario Business Registry |

> 占位符区分大小写，必须使用花括号 `{}`，拼写不可更改。

---

## 邮件主题行

文档**第一行**须以 `Subject:` 开头，系统自动提取为邮件主题（不显示在正文中）：

**EN：**
```
Subject: Annual Return Filing Reminder – {COMPANY}
```

**CN：**
```
Subject: 年度公司注册信息申报提醒 – {COMPANY}
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
| EN | Best regards, **ExampleCo Group** / Example CPA Firm P.C. Inc. / infosk@example-firm.com |
| CN | 谢谢！**ExampleCo Group** / Example CPA Firm P.C. Inc. / infosk@example-firm.com |

---

## 发送逻辑（供参考）

| 条件 | 说明 |
|---|---|
| 触发时机 | Calculated Due Date 的年份+月份 = 当前年份+月份 |
| 适用状态 | Status = Pending 或 Reminded |
| 跳过条件 | 当日已发过（ReminderSentDate = 今天） |
| 发送后状态 | 自动更新为 Reminded |

每月任意时间运行均可，仅发当月到期客户。模板不存在时系统回退至内置 HTML 模板。
