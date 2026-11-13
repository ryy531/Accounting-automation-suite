# ExampleCo AI Harness Plan

> AI Harness 设计蓝图。约束 Codex(及 Claude Code)在本仓库的工作方式。
> 文档定位:**只定规则蓝图,不直接动现有 AGENTS.md / agent / skill 文件。**
> 后续改 root AGENTS.md、子目录规则、agent、skill 都以本文档为依据。
> v4:2026-06 · 定稿候选 · 经 Codex 三轮交叉验证(skill 路径 .agents/skills / 本地文档路径 / SNAPSHOT 落盘步骤 2A)
> 真相源分两类:
> - Project 知识库(架构权威,不在仓库本地):`01_big_picture.md` / `02_roadmap.md`
> - 仓库内本地可读:`PROJECT_SNAPSHOT.md`(步骤 2A 落盘到仓库根)+ `_doc/big_picture_v1.md`

---

## 0. 这份文档是什么 / 不改什么

**是什么**
- Codex / Claude Code 在本仓库的"治理蓝图"
- 定义 harness 的目标结构、漂移修复清单、红线冗余矩阵、执行顺序

**不改什么(本文档生成阶段)**
- 不动现有 `.codex/agents/*.toml`、`.claude/agents/*.md`
- 不动现有 root `AGENTS.md` / `CLAUDE.md`
- 不创建任何子目录 AGENTS.md
- 不创建任何 skill / hooks

本文档是蓝图。Riley 确认后,**按 §5 执行顺序,一次一个文件**地落地。

**真相源说明**:`01_big_picture.md` / `02_roadmap.md` 是 Claude Project 知识库的架构权威文档,但**未必以同名存在于仓库本地**(本地搜到的是 `_doc/big_picture_v1.md`)。因此 root `AGENTS.md` 必须区分两类真相源:Project 知识库文档作为"架构权威"引用(说明它们在知识库,不是本地文件),本地可读真相源用 `PROJECT_SNAPSHOT.md` + `_doc/big_picture_v1.md`。`PROJECT_SNAPSHOT.md` 是给 AI agent 看的项目现状快照,**当前尚未落盘到仓库根**——在 §5 **步骤 2A** 落盘到仓库根,届时 root `AGENTS.md` 引用它作为本地"当前真相源"指针。

---

## 1. Codex Harness 机制速查(调研结论沉淀,经 Codex 交叉验证)

> 这一节是后续所有改动的事实基础。改 AGENTS.md / agent / skill 前先看这里,不要重新查。

### 1.1 AGENTS.md 分层加载

- Codex 启动时,从 **project root**(通常 Git 仓库根)走到**当前工作目录(CWD)**,每层取一个指导文件,按顺序加载。
- 越靠近 CWD 的文件**越晚加载**,冲突时**最具体的(最接近 CWD)文件胜出**。
- 加载顺序:global(`~/.codex/AGENTS.md`)→ project root → 中间嵌套目录 → CWD。
- 合并后总大小默认上限 **32 KiB**(`project_doc_max_bytes`),超出会被截断。

### 1.2 机制限制:只加载 root→CWD 路径,启动目录敏感

- Codex **只加载从 root 到 CWD 这条直线上的** `AGENTS.md`,**不加载旁支子目录的规则**。这是当前 discovery 机制(启动目录敏感),**不是 bug**。
- 行为:从仓库根启动去改子目录文件时,Codex **不一定会加载该子目录的 `AGENTS.md`**;需从该子目录启动 Codex 才能保证合并。
- **后果**:任何"红线"级别的约束**不能只写在子目录 AGENTS.md 里**,否则可能不被读到。
- **对策**:见 §4 红线冗余矩阵——红线必须冗余写进 root,子目录文件只做补充。
- 注:社区有 issue 追踪"从根启动应合并子目录"的预期(openai/codex#13288),未来 discovery 行为可能变化,以官方为准。

### 1.3 AGENTS.override.md 的准确语义

- `AGENTS.override.md` 在**同一目录层级**替代该目录的常规 `AGENTS.md`(同层二选一,override 优先)。
- 它**仍然参与 root→CWD 的合并链**——**不会清空父级规则**。
- 跨层冲突仍按"近 CWD 者胜"。父级(含 root)规则不被屏蔽,只是冲突项被更具体的层覆盖。
- **用途**:在某目录需要**替换本层默认指导内容**时使用(如 legacy 目录想用一套独立的本层规则)。
- ⚠️ **不要依赖 override 屏蔽 root 红线**:即便 legacy 目录用 override,root 的红线仍在合并链上。legacy 的"不重构"约束要靠机械检查(§3.5 C3)兜底,不能指望 override 排除 root 规则。

### 1.4 大小上限

- 默认 32 KiB(§1.1)。拆多文件天然规避。
- **长参考文档(如 PROJECT_SNAPSHOT.md)不要塞进 root AGENTS.md**,作为按需引用的独立文件。

### 1.5 自定义 agent 是手动调度,不是自动流水线

- `.codex/agents/*.toml` 定义的角色**不会自动接管**"架构→编码→审查"流程。
- **Codex 只在被明确要求时才 spawn subagent**,不自动派生。
- 实际运作:**Riley 在每个任务里决定调用哪个角色**(`/agent` 或 prompt 点名)。
- 这与"动代码前先说方案、确认后执行"的人工 gate 天然一致。
- ⚠️ 已知问题:项目级 `.codex/config.toml` 定义的自定义 agent 角色可能对 `spawn_agent` 不可用(config 加载回归,openai/codex#14579)。落地步骤 4 验证。

### 1.6 Skill 渐进披露

- 启动时只把 skill 的 **name + description + path** 放进 context;命中后才加载完整 `SKILL.md`。
- 隐式触发**完全依赖 description**:要简洁、scope 清晰、**触发词前置**。
- 只识别文件名严格为 `SKILL.md` 的文件。
- `[[skills.config]]`(在 `config.toml`)可禁用/启用 skill(注:项目级 config 的 skill 过滤有已知失效问题,以 `~/.codex/config.toml` 为准)。

### 1.7 机械检查靠 hooks,不靠 agent 自觉 + trust 前提

- AGENTS.md 通过**指令**塑造行为,**不是强制**。长会话可能丢失早期 context 而偏离指令。
- 红线必须有**机械检查**兜底。Codex hooks 支持生命周期点:`PreToolUse`、`PermissionRequest`、`PostToolUse`、`Stop` 等,配置在项目 `.codex/hooks.json` 或 `.codex/config.toml` 的 `[hooks]`。
- ⚠️ **hooks 是 guardrail,不是绝对执法**——官方说明 `PreToolUse` 不是完整 enforcement boundary,shell/tool 路径可能有覆盖不到的情况。hooks 是兜底,**不替代 root 红线 + 人工 gate**。
- hooks **不是写了就立刻强制执行**:项目 hooks 只在项目 `.codex/` 层 **trusted** 时加载;非 managed command hooks 还需 review/trust。
- ✅ **trust 现状**:本 UNC 项目在本机 Codex config 中已确认 trusted(Codex 2026-06 本地核实)。**但 trust 按项目路径绑定**——若仓库迁移路径 / 换机器 / 重新 clone,需重新确认 trust。
- ⚠️ 当前项目内**尚无 hooks / skills 配置**(只有 `.codex/agents` 的 toml 与 `.claude/agents` 的 md)。§3.4 / §3.5 是**从零新建**。

---

## 2. 第一优先级:漂移修复清单

> 这是最高优先级。当前规则文件仍指向旧 Tax Automation 时代和 Baserow,与新平台方向矛盾。本地现状已证实此判断。

| # | 漂移点 | 现状 | 目标 | 涉及文件 |
|---|---|---|---|---|
| D1 | 数据后端 | root AGENTS.md / CLAUDE.md 仍写 Baserow | 改为 NocoDB(走 `data_access.py`) | root `AGENTS.md`、`CLAUDE.md` |
| D2 | 旧规则引用 | agent 文件引用 `rules_tax_automation.md`、`project_tax_automation.md` | 改为引用新平台规则(本蓝图 + PROJECT_SNAPSHOT) | `.codex/agents/*.toml`、`.claude/agents/*.md` |
| D3 | 旧代码路径引用 | agent 文件引用 `src/config.py`、`src/utils.py` | 改为新平台 `_lib/` 结构;旧路径若属 legacy 则归 `Tax Automation/` 禁区 | 同上 |
| D4 | 业务方向 | 旧规则假设"个税孤岛" | 改为多业务平台(4 张表 + 业务接入规范) | root `AGENTS.md` |

**D1 是第一优先级漂移点**,先处理。

---

## 3. 目标 Harness 结构

### 3.1 root `AGENTS.md`(总规则 + 冗余红线)

只做总规则,不写成长手册。内容:

- 项目目标(一句话:多业务自动化平台,最高优先级帮 Alex 省时间)
- 当前真相源,分两类标注:
  - 本地可读:`PROJECT_SNAPSHOT.md`(步骤 2A 落盘)+ `_doc/big_picture_v1.md` + 本蓝图
  - Project 知识库(架构权威,非本地文件,勿当本地路径读):`01_big_picture.md` + `02_roadmap.md`
- **红线**(见 §4,root 必须含全部红线)
- A/B/C 触发模式(钱/邮件/对外通讯永远走 C)
- 目录地图(哪个目录是什么、哪些是禁区/冻结区)
- 何时读哪个子规则
- 出错停止原则(出错立即停,不"修了再说")
- Commit 格式:`[Phase]-[Task]: 一句话`,架构层改动加 `[ARCH-CHANGE]`

⚠️ **不要把 PROJECT_SNAPSHOT 全文塞进来**(32 KiB 上限 + context 浪费),用引用。

### 3.2 子目录规则文件清单

| 目录 | 文件类型 | 规则内容 | 理由 |
|---|---|---|---|
| `_lib/` | `AGENTS.md` | 平台层边界;connector 使用规则;禁止业务层直连 AI/email/NocoDB | 平台层补充,红线另在 root 冗余 |
| `receipts/` | `AGENTS.md` | 小票工具开发规则;测试通过后才允许发布 | 开发目录 |
| `receipts_pilot_v1.0/` | `AGENTS.override.md` | 发布目录冻结;只读不改(本层独立规则) | 本层用独立指导;**冻结靠 §3.5 C2 兜底** |
| `Tax Automation/` | `AGENTS.override.md` | legacy 维护:只修跑通,不迁移不重构(本层独立规则) | 本层用独立指导;**禁区靠 §3.5 C3 兜底,不靠 override 屏蔽 root** |
| `RealEstate/`(ExampleCo) | `AGENTS.md` | 独立项目边界;不进 4 张核心表;共享 `_lib/` 但数据独立 | ExampleCo 边界 |
| `business/`(未来) | `AGENTS.md` | 创建时再加,按业务接入规范 8 项 | 暂不建 |

**严格 scope 纪律**:每个文件只写本目录的规则,不写跨目录内容(会污染 context)。

⚠️ **两点机制约束**:
1. 子目录文件加载不可靠(§1.2)——**不能作为红线唯一载体**,红线一律在 root 冗余。
2. override 不屏蔽 root(§1.3)——legacy/冻结的强约束靠机械检查兜底,不靠 override 排除 root 规则。

### 3.3 `.codex/agents` 三角色(手动调度)

保留三类,不乱加。明确**手动调度**(§1.5),不是自动流水线。

| 角色 | 职责 | 必须输出 |
|---|---|---|
| `agent_architect` | 只设计,不写代码 | 接口契约、触发模式归属(A/B/C)、影响范围、测试门槛 |
| `agent_coder` | 只实施已确认方案 | 一次一个文件;改前 `view`;改后测试;汇报 |
| `agent_reviewer` | 只审查 | 缺陷优先;检查红线、调用链、边界、测试缺口 |

**可选第四个(以后再加)**:`agent_janitor` —— 专做小范围清理 + 规则漂移扫描,不碰业务功能。

落地动作:把三个 agent 文件从旧 Tax Automation 规则(D2/D3)切到新平台规则。

### 3.4 Skill 规划(4 个,先做 1 个 · 从零新建)

按 skill-creator 原则:短、可触发、按需加载。description 触发词前置。

**位置**:放 **repo 内 `.agents/skills/<name>/SKILL.md`**(注意是 `.agents/`,不是 `.codex/`)。官方:Codex 对仓库 skill 从 CWD 到仓库根扫描每个目录的 `.agents/skills`,根级 skill 放 `$REPO_ROOT/.agents/skills` 供任何子文件夹使用。

⚠️ **目录区分**:`.codex/` 放 agents / hooks / config;**skill 走 `.agents/skills`**,两者不同目录,不要混。放错(如 `.codex/skills`)会导致 skill 根本不被发现。

理由:skill 是项目治理资产,随仓库进 git;即便当前单人开发,放仓库内换机器/未来协作不用重建。不放 `~/.agents/skills`(个人目录,绑单用户)。

| Skill | 用途 | 优先级 |
|---|---|---|
| `ExampleCo-platform-harness` | 读项目规则、判断目录边界、判断 A/B/C 模式 | **先做这 1 个**,真实任务试跑后再拆下一个 |
| `ExampleCo-python-change` | Python 改动工作流:一文件一测一汇报 | 第二批 |
| `ExampleCo-code-review` | ExampleCo 专用审查清单 | 第二批 |
| `ExampleCo-receipts-release` | 小票工具从 `receipts/` 到 `receipts_pilot_v1.0/` 的发布流程 | 第二批 |

### 3.5 机械检查清单(hooks · 从零新建)

不靠 agent 自觉(§1.7)。前提:仓库已 trusted(§1.7 已确认)。映射到 hooks 生命周期点。

⚠️ **定位**:hooks 是 **guardrail 兜底,不是绝对执法**(§1.7)。它降低违规概率,但不替代 root 红线和人工 gate。设计时按能拦多少拦多少,不假设零漏网。

| 检查 | 内容 | 建议钩子点 |
|---|---|---|
| C1 | 扫描业务代码是否直接 import/call openai、Gemini、SMTP、IMAP、NocoDB REST(必须走 `_lib/`) | `PostToolUse`(文件写入后扫描) |
| C2 | 扫描是否改了冻结目录(`receipts_pilot_v1.0/`) | `PreToolUse`(写入前拦截) |
| C3 | 扫描是否碰了 legacy 禁区(`Tax Automation/` 的迁移/重构) | `PreToolUse` |
| C4 | 扫描 `.env`、keys、凭据是否进入 git | `PreToolUse`(git commit 前) |
| C5 | **第一版**:任务结束前必须列出测试命令 + 结果(只检查是否报告了测试证据,不验证报告真伪) | `Stop`(任务结束前校验) |

> C5 演进:第一版只查有没有报告测试命令和结果,避免一上来就解析测试框架输出判断真伪(过度复杂)。后续稳定再升级为自动判断测试是否真跑过。

---

## 4. 红线冗余矩阵

> 因为子目录 AGENTS.md 加载不可靠(§1.2)且 override 不屏蔽 root(§1.3),红线**不能只写一处**。
> 原则:**所有红线必须在 root AGENTS.md 出现**(root 一定加载);子目录文件 + hooks 是加强。

| 红线 | root AGENTS.md | 相关子目录文件 | 机械检查 |
|---|---|---|---|
| 业务层禁止直连 AI/email/NocoDB,必须走 `_lib/` | ✅ 必写 | `_lib/AGENTS.md` | C1 |
| 涉及钱/邮件/对外通讯永远走 C(人工确认) | ✅ 必写 | 各业务 AGENTS.md | — |
| 不要预先抽象(需求 ≥ 2 次才提取) | ✅ 必写 | — | — |
| schema 改在 NocoDB,不改在代码(不硬编码字段) | ✅ 必写 | `_lib/AGENTS.md` | — |
| 冻结目录只读不改 | ✅ 必写 | `receipts_pilot_v1.0/AGENTS.override.md` | C2 |
| legacy 只修跑通,不迁移不重构 | ✅ 必写 | `Tax Automation/AGENTS.override.md` | C3 |
| ExampleCo 不进 4 张核心表 | ✅ 必写 | `RealEstate/AGENTS.md` | — |
| Events 是 append-only,每个动作留痕 | ✅ 必写 | — | — |
| 凭据不进 git | ✅ 必写 | — | C4 |
| 一次一个文件,改前 view,改后测试 | ✅ 必写 | — | C5 |
| 出错立即停,不"修了再说" | ✅ 必写 | — | — |

---

## 5. 执行顺序与 checkpoint

> 一次一个文件,改完验证再继续。每步结束是一个 checkpoint。

| 步骤 | 动作 | 验证命令 / checkpoint |
|---|---|---|
| 1 | 本文档(`_doc/ai_harness_plan.md`)Riley 确认定稿 | — |
| 2A | 落盘 `PROJECT_SNAPSHOT.md` 到仓库根 | 文件存在于仓库根 |
| 2B | 更新 root `AGENTS.md`:修 Baserow→NocoDB 漂移(D1/D4)+ 写全 §4 红线 + 引用 SNAPSHOT | `codex --ask-for-approval never "Summarize the current instructions."` 应回显新规则 |
| 3 | 更新 `CLAUDE.md` 的 Baserow 漂移(Claude Code 侧,D1) | Claude Code 启动确认引用 NocoDB |
| 4 | 逐个补子目录文件(一次一个):`_lib/` → `receipts/` → `receipts_pilot_v1.0/` → `Tax Automation/` → `RealEstate/` | 从对应子目录启动 `codex --cd <dir> "Show which instruction files are active."` 确认加载 |
| 5 | 更新 `.codex/agents` 三角色,切到新平台规则(D2/D3) | 手动点名调用各角色,确认引用新规则;**同时验证项目级 agent 可被 spawn(§1.5 已知问题)** |
| 6 | 创建 `ExampleCo-platform-harness` skill(repo 内 `.agents/skills/`),真实任务试跑 | `/skills` 或 `$` 能看到该 skill;试跑后再决定是否拆第二个 |
| 7 | 补机械检查 hooks(C1-C5) | **用临时测试文件 / dry-run hook 输入**验证拦截(不真 commit,不污染 git 历史) |

> 步骤 2B、3 分别处理 Baserow 漂移(D1):Codex 侧(root AGENTS.md)与 Claude Code 侧(CLAUDE.md)各一份。
> 每步只改一个文件,改完验证再继续(步骤 2 已拆为 2A/2B 以符合此原则)。

---

## 6. 风险与未决项

| # | 风险 / 未决 | 说明 | 应对 |
|---|---|---|---|
| R1 | 子目录 AGENTS.md 加载不可靠 | root→CWD 旁支不加载(§1.2,机制非 bug) | 红线冗余进 root(§4)+ 机械检查兜底 |
| R2 | 项目级 config 的自定义 agent 可能对 spawn 不可用 | 已知 config 加载问题(§1.5,#14579) | 落地步骤 5 验证;必要时降级为手动点名 |
| R3 | trust 状态随路径绑定 | 本机已 trusted(§1.7);但迁移/换机/重 clone 后失效 | 仓库位置变更后重新确认 trust |
| R4 | 32 KiB 上限 | 多文件合并可能超限 | 不把长文档塞进 AGENTS.md;PROJECT_SNAPSHOT 用引用 |
| R5 | Codex 版本行为差异 | 上述机制基于 2026 年中版本,可能演进 | 季度用"Summarize instructions"验证一次 |
| R6 | 旧 src/ 路径归属未定 | `src/config.py`/`src/utils.py` 是否全归 legacy 禁区待确认 | 步骤 5 前与 Riley 确认 |

---

*文档结束。这是 harness 蓝图,不是 harness 本身。Riley 确认后按 §5 一步步落地。*
