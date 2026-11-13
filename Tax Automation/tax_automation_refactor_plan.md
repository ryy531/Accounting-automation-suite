# Tax Automation 重构计划
> 原则：每次只做一件事，做完测试，再做下一件。

---

## 万用开场白（每次新任务前粘贴）

```
我们正在对一个Python税务自动化项目进行渐进式重构。
规则：
1. 动代码前先告诉我你打算怎么做
2. 每次只改一个文件
3. 不要删除任何现有逻辑，只是移动/封装
4. 完成后列出所有变更
现在开始任务：[任务描述]
```

---

## 阶段零：建立安全网（第一天，必须完成才能动代码）

### 任务 0-A：建立 Git 仓库

**你自己执行（不需要Claude Code）：**
```bash
cd [你的Scripts目录]
git init
echo ".env" > .gitignore
echo "*.key" >> .gitignore
echo "*secret*" >> .gitignore
echo "__pycache__/" >> .gitignore
git add .
git commit -m "baseline: snapshot before refactor $(date +%Y%m%d)"
```
✅ 完成标志：`git log` 能看到第一条commit

---

### 任务 0-B：读取 tax_reminder.py 和 send_efile_email.py 内容

**Claude Code Prompt：**
```
请完整读取以下两个文件并告诉我：
1. 每个文件大概在做什么（一句话）
2. 使用了哪些外部库
3. 有没有hardcode的凭证、路径、邮箱地址

文件：
- Tax Automation/src/send_efile_email.py
- TaxAutomationReminderSystem/tax_reminder.py

不要修改任何内容，只读取和报告。
```

✅ 完成标志：你理解了这两个脚本在做什么

---

### 任务 0-C：扫描所有凭证位置

**Claude Code Prompt：**
```
请在 Tax Automation/src/ 目录下扫描所有Python文件，找出：
1. 所有包含 api_key, password, secret, token, fernet 关键词的行
2. 所有hardcode的邮箱地址
3. 所有hardcode的文件路径（Windows路径或UNC路径）

输出格式：
文件名 | 行号 | 问题类型 | 内容（把实际值替换为***）

不要修改任何文件。
```

✅ 完成标志：你有一张完整的"凭证地图"

---

## 阶段一：凭证统一（第1-2天）

### 任务 1-A：创建 .env 和 settings.py

**Claude Code Prompt：**
```
根据上面扫描出来的凭证列表，请做以下事情：

1. 创建 config/.env 文件，格式如下（值全部留空，我自己填）：
   GEMINI_API_KEY=
   OPENAI_API_KEY=
   EMAIL_ADDRESS=
   EMAIL_PASSWORD=
   EMAIL_IMAP_SERVER=
   EMAIL_SMTP_SERVER=
   [其他你发现的凭证]

2. 创建 config/settings.py，内容只做一件事：读取.env并暴露变量
   使用 python-dotenv 库
   如果某个必要变量缺失，要打印清晰的错误信息

3. 不要修改任何现有脚本

完成后告诉我settings.py暴露了哪些变量。
```

✅ 完成标志：`.env` 和 `settings.py` 存在，你填入真实值后能正常import

---

### 任务 1-B：逐个替换凭证（每个文件单独做）

**对每个有凭证的文件，单独发一个Prompt：**
```
请修改 [文件名]，做且只做这一件事：
- 把文件里所有hardcode的凭证/API key替换为从 config.settings 导入
- 不要改任何其他逻辑
- 改完后列出你修改了哪几行

然后我会测试这个文件是否还能正常运行。
```

**每改一个文件后你执行：**
```bash
git add [文件名]
git commit -m "config: move credentials out of [文件名]"
```

✅ 完成标志：`grep -rn "api_key\|password" Tax\ Automation/src/` 不再返回真实值

---

## 阶段二：建立基础组件（第3-5天，不动现有脚本）

### 任务 2-A：创建统一日志工具

**Claude Code Prompt：**
```
请参考以下文件中的日志实现：
- missing_client_reminder.py 中的 _load_log / _save_log
- scan_email_replies.py 中的 _load_reply_log / _save_reply_log
- send_reminder_email.py 中的 _load_log / _save_log

创建 utils/logger.py，要求：
1. 统一这三种实现为一个类或模块
2. 支持写入文件日志（JSON格式）和打印到控制台
3. 支持去重（已处理的记录不重复处理）
4. 保持向后兼容：原来的log文件格式不要变

不要修改任何现有脚本，只创建新文件。
完成后给我看 utils/logger.py 的完整内容。
```

---

### 任务 2-B：创建统一邮件客户端

**Claude Code Prompt：**
```
请参考以下文件中的邮件相关代码：
- scan_email_replies.py 中的 _connect_imap / _fetch_imap / _close_imap
- send_reminder_email.py 中的 _send_email
- send_package_email.py 中的发信部分
- missing_client_reminder.py 中的发信部分

创建 connectors/email_client.py，要求：
1. 一个 EmailClient 类
2. 方法：connect_imap(), fetch_unread(folder), send(to, subject, body, attachments=[]), close()
3. 凭证从 config.settings 读取
4. 发送失败时抛出异常，不要静默失败
5. 所有操作都要有日志输出

不要修改任何现有脚本，只创建新文件。
```

---

### 任务 2-C：创建统一 AI 客户端

**Claude Code Prompt：**
```
请参考以下文件中的AI调用代码：
- ai_rename.py 中的 load_api_key / analyze_file（Fernet加密key的部分）
- ai_extract_info.py 中的 _load_openai_key
- send_package_email.py 中的 _do_oai / _do_gemini
- send_reminder_email.py 中的 _init_gemini

创建 connectors/ai_client.py，要求：
1. 一个 AIClient 类
2. Gemini为主，OpenAI为备选（Gemini失败时自动fallback）
3. fallback时要打印警告日志，说明原因
4. 统一接口：complete(prompt, files=None) → 返回文本
5. Fernet加密逻辑从 ai_rename.py 提取到 utils/crypto.py（单独文件）
6. 凭证从 config.settings 读取

不要修改任何现有脚本，只创建新文件。
```

---

### 任务 2-D：创建统一模板引擎

**Claude Code Prompt：**
```
请参考以下文件中的模板填充实现：
- send_reminder_email.py 中的 _read_template / _fill_template
- missing_client_reminder.py 中的 _load_template / _fill_template
- send_package_email.py 中的 read_template / fill_template

创建 utils/template_engine.py，要求：
1. 一个函数：fill_template(template_path, variables: dict) → str
2. 支持中英文模板
3. 模板变量缺失时打印警告，但不崩溃（留空处理）
4. 支持字符串模板和文件路径两种输入

不要修改任何现有脚本。
```

---

### 任务 2-E：创建统一 checkpoint 工具

**Claude Code Prompt：**
```
请参考 AccessibilityCheck/accessibility_check.py 中的：
load_checkpoint / save_checkpoint / clear_checkpoint

创建 utils/checkpoint.py，要求：
1. 通用设计，任何脚本都能用
2. 用法：cp = Checkpoint("任务名"); cp.save(已处理列表); cp.load() → 已处理列表
3. checkpoint文件存在 .checkpoints/ 目录下
4. 支持清除checkpoint

不要修改任何现有脚本。
```

✅ 阶段二完成标志：5个新文件建好，git commit，现有脚本一行没动

---

## 阶段三：改造零函数脚本（第6-8天）

### 任务 3-A：重构 send_efile_email.py

**Claude Code Prompt：**
```
请修改 Tax Automation/src/send_efile_email.py，做以下事情：
1. 把所有顶层过程代码包装进 main() 函数和若干小函数
2. 把发邮件部分改用 connectors/email_client.py
3. 在文件末尾加上：if __name__ == "__main__": main()
4. 不要改变任何业务逻辑，只是重新组织结构

完成后告诉我：
- 你新增了哪些函数
- main()函数的大致流程是什么
```

**改完后测试（你手动执行）：**
```bash
python "Tax Automation/src/send_efile_email.py"
# 确认行为和之前一样
git add Tax\ Automation/src/send_efile_email.py
git commit -m "refactor: wrap send_efile_email into functions"
```

---

### 任务 3-B：重构 tax_reminder.py（同上流程）

```
请修改 TaxAutomationReminderSystem/tax_reminder.py，
做和 send_efile_email.py 相同的改造：
把顶层代码包装进函数，改用统一的email_client，不改业务逻辑。
```

---

### 任务 3-C：归档 fix_intake.py

**你自己执行：**
```bash
mkdir -p _archive
git mv "Tax Automation/src/fix_intake.py" _archive/fix_intake.py
git commit -m "archive: fix_intake no longer needed (legacy cleanup script)"
```

---

## 阶段四：拆解 send_package_email.py（第9-12天）

> 这是最复杂的文件，分三次拆，每次只做一件事。

### 任务 4-A：只提取 PDF 扫描部分

**Claude Code Prompt：**
```
send_package_email.py 有30+个函数，我们分三次拆它。
第一次，只做这一件事：

把以下函数提取到新文件 connectors/pdf_client.py：
- scan_taxcycle_pdfs
- find_t1_pdf
- extract_t1_info
- group_pdfs_by_person

要求：
1. 新文件包含这些函数，从settings读取必要配置
2. send_package_email.py 改为从 connectors.pdf_client import 这些函数
3. 其他函数不动
4. 行为完全不变

完成后运行原脚本确认没有报错。
```

### 任务 4-B：提取模板填充部分

```
第二次，只做这一件事：
把 send_package_email.py 中的 read_template / fill_template
改为调用已有的 utils/template_engine.py
删除文件内重复的实现。
其他不动。
```

### 任务 4-C：发信部分改用 email_client

```
第三次，只做这一件事：
把 send_package_email.py 中的 send_one 函数
改为调用 connectors/email_client.py 的 send() 方法。
删除文件内重复的SMTP代码。
其他不动。
```

---

## 阶段五：合并重复逻辑（第13-15天）

### 任务 5-A：合并客户匹配逻辑

**Claude Code Prompt：**
```
请分析以下两个文件的职责重叠：
- Tax Automation/src/client_lookup.py（函数：load_master, build_name_index, lookup_client）
- Tax Automation/src/name_mapping.py（函数：scan_client_folders, best_folder_match, build_mapping）

创建 core/client_matching.py，合并两者，要求：
1. 保留所有现有功能
2. 删除重复的姓名匹配逻辑，统一为一个实现
3. 原两个文件改为从 core.client_matching import，保持接口不变（向后兼容）

分析完先告诉我你的合并方案，等我确认再动代码。
```

### 任务 5-B：合并文件操作逻辑

```
分析 create_folders.py 和 rename_cra_files.py 的重叠部分，
创建 core/file_operations.py 合并共用逻辑。
同样先说方案再动代码。
```

---

## .bat 启动器更新（贯穿全程）

每完成一个阶段，用这个Prompt更新bat文件：

**Claude Code Prompt：**
```
请检查现有的 .bat 启动器文件，确认以下脚本的调用方式仍然正确：
[列出这个阶段改动过的脚本名]

如果有路径或调用方式需要更新，告诉我哪里需要改，不要自动修改。
```

---

## 每次任务后的固定检查（30秒）

```bash
# 1. 确认没有新增hardcode凭证
grep -rn "api_key\s*=" Tax\ Automation/src/ | grep -v "settings\|import\|#"

# 2. 确认可以正常import
python -c "from config.settings import *; print('settings OK')"

# 3. 提交
git add -A
git commit -m "[阶段]-[任务]: [一句话描述做了什么]"
```

---

## 进度追踪

| 阶段 | 任务 | 状态 |
|------|------|------|
| 0 | git init + baseline commit | ⬜ |
| 0 | 读取零函数脚本内容 | ⬜ |
| 0 | 扫描凭证位置 | ⬜ |
| 1 | 创建 .env + settings.py | ⬜ |
| 1 | 替换各文件凭证 | ⬜ |
| 2 | utils/logger.py | ⬜ |
| 2 | connectors/email_client.py | ⬜ |
| 2 | connectors/ai_client.py | ⬜ |
| 2 | utils/template_engine.py | ⬜ |
| 2 | utils/checkpoint.py | ⬜ |
| 3 | send_efile_email.py 重构 | ⬜ |
| 3 | tax_reminder.py 重构 | ⬜ |
| 3 | fix_intake.py 归档 | ⬜ |
| 4 | send_package_email 拆分（3次）| ⬜ |
| 5 | core/client_matching.py | ⬜ |
| 5 | core/file_operations.py | ⬜ |
