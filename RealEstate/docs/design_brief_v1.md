# Real Estate Bulk Email — 设计简报 v1

> 日期：2026-05-11
> 状态：架构设计阶段，待 Riley 确认后进入 Phase 1 开发

---

## 1. 项目背景

ExampleCo 地产业务需要向 agent 列表（约 2000 人）批量发送 listing 推广邮件。每天选取 100 人发送，20 天为一个周期。邮件内容来自地产 listing PDF（含图片、文字信息）以及公司网站上对应的房源链接。

### 1.1 核心约束

- **合规**：邮件必须包含退订功能（CASL）
- **红线**：发邮件走 `_lib/connectors/email_client.py`，不直接调 SMTP
- **触发模式 C**：批量准备草稿 → 人工审核 → 批量发送（CLAUDE.md 规定）
- **去重**：同一人不会收到同一 listing 的邮件两次
- **不预先抽象**：第一阶段硬编码，等确实需要再升级

---

## 2. 业务规则

### 2.1 周期调度

| 参数 | 值 | 说明 |
|------|-----|------|
| 每日发送量 | 100 封 | 从 pool 中随机选取 |
| 周期长度 | 20 天 | 到期后归档当前周期 |
| Pool 重置 | 每个新周期开始时 | 从完整 agent 列表重新开始 |
| Listing 生命周期 | ~20 天（一个周期） | 多 listing 轮换规则待定 |

### 2.2 选人逻辑

```
每日执行时：
1. 加载完整 agent 列表（CSV）
2. 排除 status = unsubscribed 的 agent
3. 排除在当前 listing 的 SendRecord 中已出现的 agent
4. 从剩余 pool 中随机选取 min(100, len(pool)) 人
5. 发送后写入 SendRecord
```

### 2.3 Listing 管理

- Listing PDF 存放在专用文件夹
- 支持同时存在多个 PDF（多 listing 场景）
- 第一阶段：一个周期绑定一个 listing
- 未来扩展：一天可发多个 listing，每人每天最多收到一个

### 2.4 退订规则

- Agent 点击邮件中的退订链接 → 全局退订（所有 listing 不再收到）
- 退订状态持久化，跨周期有效
- 退订 token 生成：`hashlib.sha256(email + SECRET_SALT).hexdigest()[:16]`

---

## 3. 数据模型

### 3.1 实体关系

```
Agent (1) ──────< SendRecord >────── (1) Listing
                                           │
                                           │
                                      Cycle (1)
```

### 3.2 Agent

| 字段 | 类型 | 说明 |
|------|------|------|
| email | string (PK) | Agent 邮箱 |
| name | string | 姓名 |
| status | enum | `active` / `unsubscribed` |
| unsub_token | string | 退订验证用 token |
| unsub_date | date | 退订日期（null 表示未退订） |
| source_file | string | 来源 CSV 文件名 |

### 3.3 Listing

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string (PK) | 唯一标识（可从文件名派生） |
| name | string | Listing 名称 |
| pdf_path | string | PDF 文件完整路径 |
| image_path | string | 提取的图片路径 |
| website_url | string | 公司网站上对应的房源链接 |
| extracted_info | dict | AI 从 PDF 提取的结构化信息 |
| folder_path | string | PDF 所在文件夹路径 |
| created_date | date | 放入文件夹的日期 |

### 3.4 Cycle

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int (PK) | 自增 |
| listing_id | string (FK) | 绑定的 listing |
| start_date | date | 周期开始日期 |
| day_count | int | 已执行天数 |
| status | enum | `active` / `completed` |

### 3.5 SendRecord

| 字段 | 类型 | 说明 |
|------|------|------|
| agent_email | string (FK) | Agent 邮箱 |
| listing_id | string (FK) | Listing ID |
| cycle_id | int (FK) | 周期 ID |
| sent_date | datetime | 发送时间 |
| status | enum | `sent` / `failed` / `skipped` |

**唯一约束：** `(agent_email, listing_id)` — 保证同一人不会收到同一 listing 两次。

---

## 4. 状态机 — Cycle 生命周期

```
                    [Listing PDF 放入文件夹]
                              │
                              │  scheduler 检测到新 listing
                              │
                    ┌─────────▼─────────┐
                    │  Cycle: ACTIVE     │
                    │  day_count = 0     │
                    │  pool = 全量 agents│
                    └─────────┬─────────┘
                              │
              ┌───────────────▼────────────────┐
              │  每日 Cron 触发                  │
              │                                 │
              │  1. 从 Hostinger 拉取退订列表    │
              │  2. 合并到本地退订记录            │
              │  3. Pool = 全量 - 已发 - 已退订  │
              │  4. 随机选 min(100, len(pool))   │
              │  5. 组装邮件                     │
              │  6. Mode C: 草稿 → 审核 → 发送   │
              │  7. 写 SendRecord               │
              │  8. day_count += 1               │
              │  9. 发送成功摘要邮件给 Riley       │
              └───────────────┬────────────────┘
                              │
                        day_count >= 20 ?
                        ┌─── NO ──┘  └─── YES ───┐
                        │                         │
                        │ (继续下一轮)    ┌────────▼────────┐
                        │                │ Cycle: COMPLETED │
                        │                │ 归档 SendRecord  │
                        │                │ 剩余 agent 不管  │
                        │                └────────┬────────┘
                        │                         │
                        │              新 listing 放入文件夹
                        │                         │
                        │              ┌──────────▼──────────┐
                        └──────────────│ 新 Cycle: ACTIVE    │
                                       │ pool 重置（全量）    │
                                       └─────────────────────┘
```

---

## 5. 模块结构

```
RealEstate/
├── config.py               ← 参数与路径配置
├── models.py               ← 数据结构定义 + 存储读写（CSV/JSON 第一阶段）
├── agent_pool.py           ← Agent 列表解析、筛选、去重、随机选取
├── listing.py              ← 监听文件夹、PDF 解析、图片提取、网站爬取
├── email_builder.py        ← 邮件内容组装（Listing 数据 + 模板 + 退订链接）
├── scheduler.py            ← 每日调度：选人 → 建邮件 → 调 Mode C 流程
├── unsubscribe.py          ← 从 Hostinger 拉取退订列表 + 本地合并
└── main.py                 ← CLI 入口 + try/except 告警

数据文件（第一阶段）：
RealEstate/
├── data/
│   ├── agents.csv              ← Agent 列表（外部提供）
│   ├── unsubscribe.json        ← 本地退订记录
│   ├── listings/               ← Listing PDF 存放文件夹
│   ├── send_records/           ← 按周期存放发送记录
│   │   ├── cycle_001.json
│   │   └── cycle_002.json
│   └── cycles.json             ← 周期状态追踪
└── logs/                       ← 运行日志
```

### 5.1 依赖关系图

```
main.py
  └→ scheduler.py
       ├→ unsubscribe.py → models.py
       ├→ agent_pool.py  → models.py
       ├→ listing.py     → models.py, _lib/connectors/ai_client.py
       └→ email_builder.py → models.py
                             ↑
              _lib/connectors/email_client.py  ← 唯一发邮件出口
              _lib/utils/logger.py             ← 日志
```

### 5.2 外部依赖

| 依赖 | 用途 | 来源 |
|------|------|------|
| `_lib/connectors/email_client.py` | SMTP 发邮件 / IMAP 读邮件 | 已有 |
| `_lib/connectors/ai_client.py` | 从 PDF 提取结构化信息 | 已有 |
| `_lib/utils/logger.py` | JSON 日志 | 已有 |
| `_lib/config/settings.py` | 凭据（邮箱、AI API key） | 已有 |
| `openpyxl` / `csv` | 读写 CSV | Python 内置 / 已安装 |
| `pdfplumber` 或 `PyMuPDF` | PDF 解析 + 图片提取 | 待安装 |
| `requests` + `BeautifulSoup` | 爬取公司网站房源链接 | 待确认是否已安装 |
| `Pillow` | 图片处理（如需裁剪/缩放） | 待确认 |

---

## 6. 接口契约

### 6.1 config.py

```python
# 路径配置
AGENT_CSV_PATH = "data/agents.csv"
LISTING_FOLDER = "data/listings/"
SEND_RECORDS_FOLDER = "data/send_records/"
CYCLES_FILE = "data/cycles.json"
UNSUBSCRIBE_FILE = "data/unsubscribe.json"
LOG_FOLDER = "logs/"

# 调度参数
DAILY_SEND_COUNT = 100
CYCLE_MAX_DAYS = 20
SECRET_SALT = "xxx"  # 退订 token 生成用

# Hostinger 退订
HOSTINGER_UNSUB_URL = "https://example-firm.com/data/unsubscribe.json"

# 邮件配置（走 _lib，这里只放模板路径）
EMAIL_TEMPLATE_PATH = "templates/"

# 公司网站
COMPANY_WEBSITE_BASE = "https://example-firm.com"
```

### 6.2 models.py — 核心接口

```python
# Agent 操作
def load_agents(csv_path: str) -> list[dict]
def get_unsubscribed_emails(unsub_file: str) -> set[str]
def is_already_sent(email: str, listing_id: str, records_folder: str) -> bool
def generate_unsub_token(email: str) -> str

# Listing 操作
def get_active_cycle(cycles_file: str) -> dict | None
def create_cycle(listing_id: str, cycles_file: str) -> dict
def complete_cycle(cycle_id: int, cycles_file: str) -> None
def scan_listing_folder(folder_path: str) -> list[dict]

# SendRecord 操作
def save_send_records(records: list[dict], cycle_id: int, records_folder: str) -> None
def get_sent_emails(listing_id: str, records_folder: str) -> set[str]
```

### 6.3 agent_pool.py

```python
def select_daily_pool(
    agents: list[dict],            # 全量 agent 列表
    listing_id: str,               # 当前 listing
    unsubscribed: set[str],        # 已退订的邮箱
    sent_emails: set[str],         # 已发送过的邮箱
    count: int = 100               # 每日选取数量
) -> list[dict]                    # 返回被选中的 agent 列表
```

### 6.4 listing.py

```python
def discover_new_listings(folder_path: str, known_ids: set[str]) -> list[dict]
def extract_listing_info(pdf_path: str) -> dict
    # 返回: {"text": "...", "images": [...], "price": "...", "address": "...", ...}

def extract_featured_image(pdf_path: str, output_dir: str) -> str
    # 返回: 图片文件路径

def match_website_url(listing_info: dict, base_url: str) -> str | None
    # 根据提取的信息（地址等），在公司网站上搜索匹配的房源链接
```

### 6.5 email_builder.py

```python
def build_email(
    agent: dict,           # 收件人信息
    listing: dict,         # listing 数据（info + image + url）
    unsub_token: str,      # 退订 token
) -> dict
    # 返回: {"to": "...", "subject": "...", "html_body": "...", "attachments": [...]}

def build_summary_email(
    sent_count: int,
    failed_count: int,
    cycle_day: int,
    listing_name: str,
) -> dict
    # 返回: 每日摘要邮件结构（发给 Riley）

def build_error_email(error_trace: str) -> dict
    # 返回: 告警邮件结构（发给 Riley）
```

### 6.6 scheduler.py

```python
def run_daily_job() -> None
    # 主调度逻辑：
    # 1. 拉取退订列表
    # 2. 检查/创建 cycle
    # 3. 发现/解析 listing
    # 4. 选人
    # 5. 组装邮件
    # 6. Mode C: 写草稿 / 发送
    # 7. 记录 SendRecord
    # 8. 更新 cycle 状态
    # 9. 发送摘要
```

### 6.7 unsubscribe.py

```python
def fetch_remote_unsub_list(url: str) -> list[dict]
    # 从 Hostinger 拉取退订 JSON

def merge_unsub_list(local_path: str, remote_list: list[dict]) -> set[str]
    # 合并远程 + 本地退订列表，返回邮箱集合

def mark_local_unsubscribed(email: str, local_path: str) -> None
    # 本地标记退订（兜底用）
```

---

## 7. 退订系统设计

### 7.1 架构

```
Agent 收到邮件
    │
    │  点击邮件底部 "Unsubscribe" 链接
    │  链接格式: https://example-firm.com/unsubscribe?token=abc123def456
    │
    ▼
┌──────────────────────────┐
│  Hostinger (公网)         │
│                          │
│  /unsubscribe/index.php  │
│    ├─ 验证 token         │
│    ├─ 反算出 email       │
│    ├─ 写入 unsub 数据    │
│    └─ 显示确认页面       │
│                          │
│  /data/unsubscribe.json  │
│    [{"email":"x@y.com",  │
│      "date":"2026-05-11"}│
│     ...]                 │
└──────────────────────────┘
    │
    │  每日 Cron 启动时 curl 拉取
    │
    ▼
┌──────────────────────────┐
│  Windows 服务器 (内网)    │
│                          │
│  unsubscribe.py          │
│    拉取 JSON → 合并到    │
│    本地 unsub 记录       │
│    → 排除这些人          │
└──────────────────────────┘
```

### 7.2 Token 设计

```python
import hashlib

SECRET_SALT = "从 config.py 读取"

def generate_unsub_token(email: str) -> str:
    return hashlib.sha256(f"{email}{SECRET_SALT}".encode()).hexdigest()[:16]

def verify_unsub_token(token: str, email: str) -> bool:
    return generate_unsub_token(email) == token
```

PHP 端用相同算法验证。不需要数据库映射表。

### 7.3 PHP 退订页面（Hostinger 端）

```php
// /unsubscribe/index.php
// 1. 读取 token 参数
// 2. 遍历已知 agent 列表（或用相同 hash 算法反推）
// 3. 验证 token 有效
// 4. 写入 /data/unsubscribe.json
// 5. 显示 "You have been unsubscribed" 页面
```

> 详细 PHP 代码在 Phase 3 实现时编写。

---

## 8. Cron 配置与故障兜底

### 8.1 Windows Task Scheduler 配置

```
任务名：RealEstate_DailyEmail
触发器：每天早上 9:00 AM
操作：python "Z:\Scripts\RealEstate\main.py"
条件：不管用户是否登录都运行
设置：如果任务运行超过 30 分钟则停止
```

### 8.2 故障检测机制

```
main.py 执行流程（包裹在 try/except 中）：

try:
    1. 拉取退订列表
    2. 选人 + 组装邮件
    3. Mode C 审核 + 发送
    4. 写 SendRecord
    5. 发送成功摘要邮件给 Riley    ← 每日心跳
except Exception as e:
    发送失败告警邮件给 Riley        ← 包含 traceback
    写入 error log
```

**两层保障：**

| 场景 | 检测方式 | Riley 如何得知 |
|------|----------|--------------|
| 程序报错 | except 捕获 | 收到告警邮件 |
| 整机宕机 | 无心跳 | 没收到每日摘要邮件 = 出事了 |
| 程序卡死 | Task Scheduler 30 分钟超时 | 下次检查任务历史 |

---

## 9. 数据文件格式（第一阶段）

### 9.1 agents.csv

```csv
email,name,phone,company
agent1@example.com,John Doe,604-123-4567,ABC Realty
agent2@example.com,Jane Smith,604-234-5678,XYZ Properties
...
```

### 9.2 cycles.json

```json
[
  {
    "id": 1,
    "listing_id": "listing_20260511_001",
    "start_date": "2026-05-11",
    "day_count": 0,
    "status": "active"
  }
]
```

### 9.3 send_records/cycle_001.json

```json
[
  {
    "agent_email": "agent1@example.com",
    "listing_id": "listing_20260511_001",
    "cycle_id": 1,
    "sent_date": "2026-05-11T09:05:00",
    "status": "sent"
  }
]
```

### 9.4 unsubscribe.json（本地 + Hostinger 同格式）

```json
[
  {
    "email": "agent5@example.com",
    "date": "2026-05-12",
    "source": "web"
  }
]
```

---

## 10. Multi-Listing 扩展预留

第一阶段：一天一个 listing，一个周期一个 listing。

未来扩展点（**不预先实现，只留数据模型余地**）：

| 扩展场景 | 需要改的地方 | 不需要改的地方 |
|----------|-------------|---------------|
| 一天发多个 listing | `scheduler.py` 增加循环 | `agent_pool.py`, `email_builder.py` |
| 不同 listing 发不同人 | `scheduler.py` 分配逻辑 | `models.py`（SendRecord 已有 listing_id） |
| 同一 listing 多周期重发 | `scheduler.py` 创建新 cycle | 去重逻辑（按 listing_id + cycle_id 隔离） |

**核心设计决策：** 去重键是 `(agent_email, listing_id)` 而不是 `(agent_email, cycle_id)`。这意味着同一 listing 永远不会发给同一个人，即使跨周期。如果未来需要"同 listing 重新推一次"，再调整。

---

## 11. 实施阶段

### Phase 1：数据管道（约 2-3 天）

**目标：** 跑通选人 + PDF 解析 + 数据记录，不发邮件。

| 任务 | 文件 | 交付物 |
|------|------|--------|
| 项目骨架 + config | `config.py` | 参数配置 |
| 数据模型 + 存储 | `models.py` | CSV/JSON 读写 |
| Agent 列表解析 | `agent_pool.py` | 读 CSV + 随机选人 + 去重 |
| Listing 文件夹监听 | `listing.py` | 发现新 PDF |
| PDF 解析 + 图片提取 | `listing.py` | AI 提取 + 图片导出 |
| 网站爬取匹配 | `listing.py` | 地址 → URL 匹配 |
| 测试 | 手动运行 | 验证数据管道正确 |

### Phase 2：邮件 + 调度（约 2-3 天）

**目标：** 组装邮件内容 + 每日调度逻辑 + Mode C 流程。

| 任务 | 文件 | 交付物 |
|------|------|--------|
| 邮件组装 | `email_builder.py` | HTML 邮件 + 退订链接 |
| 每日调度 | `scheduler.py` | 选人 → 建邮件 → 发送 |
| CLI 入口 + 告警 | `main.py` | try/except + 摘要/告警邮件 |
| Mode C 流程 | `scheduler.py` | 草稿 → 审核 → 发送 |
| 集成测试 | 手动触发 | 端到端验证 |

### Phase 3：退订 + 生产化（约 2-3 天）

**目标：** 退订页面上线 + Task Scheduler 配置 + 日志完善。

| 任务 | 文件 | 交付物 |
|------|------|--------|
| Hostinger 退订页面 | PHP | 公网可访问的退订页 |
| 退订列表拉取 | `unsubscribe.py` | curl + 合并逻辑 |
| Windows Task Scheduler | 系统配置 | 每日自动触发 |
| 日志 + 监控 | `main.py` | 完善日志 + 心跳邮件 |
| 生产验收 | 全流程测试 | Riley 确认可上线 |

---

## 12. 风险与待定项

| 项目 | 风险等级 | 说明 | 处理方案 |
|------|---------|------|---------|
| PDF 解析准确性 | 中 | 地产 PDF 格式可能不统一 | AI 提取 + 人工校验（Mode C） |
| 网站爬取稳定性 | 中 | 网站结构变化导致匹配失败 | 缓存 + 人工 fallback |
| Hostinger 安全性 | 低 | unsubscribe.json 可能被直接访问 | .htaccess 限制或改用 API |
| 邮件被标记为垃圾邮件 | 高 | 100 封/天 可能触发 SMTP 限流 | 确认 Hostinger SMTP 限额，必要时分批发送 |
| Agent 列表格式变化 | 低 | 对方可能换格式 | 第一阶段硬编码，报错则手动调整 |

---

## 13. 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-05-11 | 退订方案选 B（Web 页面） | 专业、用户体验好 |
| 2026-05-11 | 数据同步用 Hostinger 文件拉取 | 最低复杂度，符合不预先抽象原则 |
| 2026-05-11 | 触发方式用 Windows Task Scheduler | 用户可接受不关机 |
| 2026-05-11 | Multi-listing 暂不实现 | 需求未定，数据模型已留余地 |
| 2026-05-11 | 第一阶段存储用 CSV/JSON | 快速落地，等确实需要再升级 SQLite |
| 2026-05-11 | 去重键 = (agent_email, listing_id) | 跨周期保证不重复，未来可调整 |
| 2026-05-11 | 邮件默认英文 | 业务需求，未来支持模板 |
