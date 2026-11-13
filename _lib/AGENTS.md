# `_lib/` 平台层规则

`_lib/` 是所有业务复用的平台层。这里的代码提供稳定接口，不承载单个业务流程。

## 边界

- `connectors/` 封装外部服务：AI、email、NocoDB/数据后端等
- `data_access.py` 是业务代码访问数据的接口契约
- `server/` 承载 Flask Webhook 和 cron 入口
- `utils/` 只放跨业务且已出现重复需求的工具
- `tools/` 放平台级只读诊断 / 运维脚本；只读 metadata / health check 可直接使用 connectors，业务读写仍优先走 `data_access.py`

## 红线

- 不要让业务代码绕过 `_lib/connectors/ai_client.py` 直接调用 Gemini / OpenAI
- 不要让业务代码绕过 `_lib/connectors/email_client.py` 直接 SMTP / IMAP
- 不要让业务代码直连 NocoDB REST API；业务层必须走 `_lib/data_access.py`
- 不要在代码里硬编码 NocoDB 字段列表；schema 变更优先在 NocoDB 产品里做
- 不要恢复 `_lib/connectors/baserow_client.py`;正式数据访问走 `_lib/data_access.py` + `nocodb_client.py`
- 涉及钱、邮件、对外通讯的能力只能用于 C 模式：自动准备 + 人工确认

## 修改原则

- 已稳定模块只做必要局部 patch，不顺手重写
- 新公共能力只有在同类需求出现 >= 2 次后才提取
- 所有外部 I/O 必须有 timeout / retry / 明确错误处理
- 日志不得记录 SIN、API key、邮箱密码、完整客户敏感资料
- 运行报告只写 gitignored 路径，例如 `_reports/`，不提交报告全文

## 验证

- 改 `_lib` 后至少跑相关 `_lib/tests` 或最小 import/compile 检查
- 改 connector 时优先使用 mock / dry-run，不直接触发真实邮件、真实外部写入或生产数据修改
