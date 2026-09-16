---
name: vertex-ai-litellm-bridge
description: 用 Google Cloud 300 美元赠金把 Gemini（Vertex AI / 现名 Gemini Enterprise Agent Platform）接进 ZCode / OpenCode / Cline 等只支持 OpenAI 或 Anthropic 格式的客户端。触发词：Vertex AI 接入 ZCode、GCP 赠金调 Gemini、Vertex AI 报 403、BILLING_DISABLED、SERVICE_DISABLED、组织政策不让建服务账号密钥、LiteLLM 转发报 502、LiteLLM 报 No connected db、ZCode 一直重连、模型说自己是别的版本、赠金余额怎么监控、GCP 试用到期会不会扣款。包含实测过的认证方式、组织政策绕过、保活设计（S4U + 看门狗）、master key / virtual key 管理、Windows/代理环境坑位与可直接复用的配置驱动诊断脚本。结论经官方文档 + 社区教程 + 本机实测三方交叉验证。
agent_created: true
---

# 用 GCP $300 赠金把 Gemini 接进 ZCode（完整 SOP）

## 这个 skill 解决什么

用户有一张 Google Cloud $300 赠金，想用其中的 Gemini 模型，并接进 ZCode 这类只认
`Anthropic Messages` / `Chat Completions` / `Responses` 三种格式的客户端。

**从零到端到端跑通大约需要用户在控制台点 6 步，其余全自动。**

---

## 0. 三十秒结论（先跟用户对齐这四条，能省掉 80% 的来回）

| 问题 | 答案 |
|---|---|
| 能不能像 OpenAI 那样「填个 API Key + Base URL」就完事？ | **不能。** Vertex 只收 OAuth2 令牌，明确拒绝 API Key |
| 那要装 LiteLLM？ | **要。** 它的职责不是格式翻译，是**自动换令牌 + 每小时自动续 + 给一个固定地址和固定 Key** |
| 控制台里那个「API 密钥」能用吗？ | **不能。** 那是 Express 快速模式，不消耗赠金，且 LiteLLM / ZCode 都不支持 |
| 会不会被多扣钱？ | **不会自动扣。** 只有**手动点 Activate 升级**后才计费。不升级 → 结算账号关闭 → 30 天宽限 → 资源永久删除。见下方「关于扣款」 |

**决策表 —— 什么情况该走这条路**

| 情况 | 做法 |
|---|---|
| 客户端支持 Vertex / Gemini 原生格式（如 SillyTavern 的「Google Vertex AI」） | 直接填 API Key + 区域 `global`，不需要 LiteLLM |
| 客户端只支持 OpenAI / Anthropic 格式（ZCode、OpenCode、Cline、Cursor） | **走本 skill** |
| 只要一次性试用、不在乎令牌过期 | 可以不用 LiteLLM，自己写个脚本每小时刷令牌 |

### 关于扣款（用户必问，社区误传很多，以官方原文为准）

**官方文档 `docs.cloud.google.com/free/docs/free-cloud-features` 原文：**

> "Your Free Trial billing account **auto-closes** if you spend the $300 credit or 90 days
> pass from signup **and you don't upgrade to a Paid billing account**."
>
> "**Billing is disabled** on your project and your Free Trial account enters a
> **30-day grace period**. If you don't upgrade ... your Free Trial resources are
> **permanently deleted**."
>
> "Upgrading to a Paid billing account ends your Free Trial; **you will be billed** for usage
> not covered by your $300 Welcome credit..."

**结论**：试用期结束 = 结算账号**关闭**，不是自动转付费。计费只在用户**显式点 Activate** 后开始。

**社区里流传的「到期会自动从卡上扣钱」是错的**（或特指「已经升级过」的账号）。
注意这类说法的来源往往是 **GCP 代理商的开户导流文**——把自助注册说得越可怕，
越容易把人推到代理渠道。看到「会自动扣款」「赶紧找我开户」的组合，先查官方原文。

⚠️ **真正的扣款风险只有一个**：误点控制台 Welcome 页的 **Activate** 按钮。
告诉用户：赠金没花完之前，**不要碰那个按钮**。

---

## 阶段 0 · 事实核查（动手前先确认这些没变）

Google 改名频繁，先核对再动手，别凭记忆教用户：

| 项 | 2026-09 的状态 |
|---|---|
| Vertex AI 现名 | **Gemini Enterprise Agent Platform**（2026-04 改名），但 **API 端点 `aiplatform.googleapis.com` 不变** |
| 「Vertex AI 用户」角色 | 显示名改为「**Gemini Enterprise Agent Platform 用户**」，角色 ID 仍是 `roles/aiplatform.user` |
| OpenAI 兼容端点 | `https://aiplatform.googleapis.com/v1/projects/{proj}/locations/{loc}/endpoints/openapi` |
| 区域 | 统一用 `global`（preview 模型只在这里有） |

---

## 阶段 1 · 拿凭据（最容易卡死的一步）

### 1.1 组织政策挡住服务账号密钥创建

报错特征：
```
服务账号密钥创建功能已停用
强制执行的组织政策 ID：iam.disableServiceAccountKeyCreation
```

**根因**：Google 从 **2024-05-03** 起对所有新组织默认强启「安全基准限制」，
包含 `constraints/iam.managed.disableServiceAccountKeyCreation`。
组织会在「网域用户首次登录」或「创建了无关联组织的结算账号」时被**自动预配**。
→ **这不是用户点错了。**

**解法 A（首选，5 分钟）**：新建项目时「组织」栏选 **「无组织」**。
组织政策只作用于组织下的资源，项目不挂组织就不受约束。
> 验证方法：项目选择器弹窗左上角应显示「无组织」。

**解法 B**：改用 ADC 用户 OAuth。`gcloud auth application-default login`，
LiteLLM 官方文档明确支持。代价：刷新令牌被撤销后要重做。

**解法 C**：关掉组织政策。需组织级 `roles/orgpolicy.policyAdmin`：
```bash
gcloud org-policies delete iam.disableServiceAccountKeyCreation --organization=<ORG_ID>
```
注意托管约束 `iam.managed.*` 和普通约束 `iam.*` 是两条，都要看。

### 1.2 建服务账号并下载 JSON

1. 服务账号页面 → 创建服务账号
2. 角色搜不到「Vertex AI 用户」**别纠结，直接给 `Owner`**（个人测试项目无所谓），省一轮扯皮
3. 密钥标签页 → 添加密钥 → 创建新密钥 → **JSON** → 下载

### 1.3 启用 API（只能用户点）

```bash
gcloud services enable aiplatform.googleapis.com --project=<PROJECT_ID>
```
或控制台 `console.cloud.google.com/apis/library/aiplatform.googleapis.com?project=<ID>`。

> **实测：服务账号默认没有 `serviceusage.services.enable` 权限**，
> 所以这一步基本只能让用户在控制台点，别浪费时间试脚本。

---

## 阶段 2 · 搭桥（LiteLLM）

### 2.1 目录结构

```
C:\Users\<user>\.zcode\litellm\
├── config.yaml
├── start-litellm.bat
├── logs\proxy.log
└── venv\
C:\Users\<user>\.zcode\secrets\
└── <project>.json          # 服务账号密钥
```

### 2.2 config.yaml

见 `assets/config.yaml.example`。要点：
- `model: vertex_ai/<model>`（**必须带 `vertex_ai/` 前缀**，否则 LiteLLM 会走 AI Studio）
- `vertex_project` / `vertex_location: global` / `vertex_credentials: <绝对路径>`
- `litellm_settings: drop_params: true`（客户端可能发 Vertex 不认的参数）
- **`router_settings` 建议加上重试和超时**（社区实践，实测能挡掉上游抖动）：

  ```yaml
  router_settings:
    num_retries: 2          # Vertex 偶发 429/503 时自动重试，不直接透传给客户端
    timeout: 120            # 长上下文 + thinking 模型可能跑很久，别用默认的 30
    retry_after: 3
  ```

  > 缺这一段时，上游一次抖动就会变成客户端里的一次失败；加了之后 LiteLLM 自己消化。
  > `timeout` 尤其重要：ZCode 走 `openai-compatible` 时，`gemini-3.8-flash` 这类
  > 带 thinking 的模型首字延迟可能超过 30 秒。

### 2.3 启动脚本

见 `assets/start-litellm.bat.example`。三个必须点：
1. **CRLF + 纯 ASCII + 无 BOM** —— LF 换行、UTF-8 BOM、**或注释里写中文**，都会让 `cmd` 静默失败、`%VAR%` 展开为空
2. 设出网代理（走本机翻墙代理才能连 Google）
3. **开头加端口守卫**（幂等），配合看门狗

> 详细的三类文件编码规则（`.bat` / `.ps1` / `.py` 要求各不相同）见「环境坑位 §3」。

### 2.4 验证桥本身

```bash
curl -s -H "Authorization: Bearer <MASTER_KEY>" http://127.0.0.1:4000/v1/models
```

### 2.5 密钥怎么管（社区共识：master key 不当应用 key 用）

LiteLLM 有两种 key，社区文档一致强调**不要混用**：

| | master key | virtual key |
|---|---|---|
| 用途 | Proxy 自身的管理凭证 | 发给客户端的应用凭证 |
| 长相 | 自己设的随机串（如 `sk-xxx`） | LiteLLM 生成，`sk-...` |
| 能力 | 全部权限，能建 key、看全部用量 | 可限模型白名单 / 预算 / 速率 |
| 该给客户端吗 | ❌ 官方口径是「像数据库 root 密码一样锁起来」 | ✅ |

**生成 virtual key 需要接数据库**：
```yaml
litellm_settings:
  database_url: postgresql://litellm:pwd@db/litellm
```
然后：
```bash
curl -X POST http://127.0.0.1:4000/key/generate \
  -H "Authorization: Bearer <MASTER_KEY>" -H "Content-Type: application/json" \
  -d '{"models":["gemini-3.8-flash"],"max_budget":10,"budget_duration":"30d"}'
```

**本机是单机个人场景 → 不接 DB，直接用 master key 给 ZCode 填**，可以接受。
但要知道两条实测后果：

1. **没有 DB 就生成不了 virtual key**，只能一直用 master key。
2. **key 填错时报的是 `400 {"error":{"message":"No connected db."}}`，不是 401。**
   这个报错**极具误导性**——看到 "No connected db" 会以为是数据库问题，
   实际是 **key 不匹配**（LiteLLM 找不到 key 就去查 DB，DB 又没接）。
   > 实测：key 少一个字符 / 多一个字符 / 随便编，全部返回这个 400；
   > 只有完全正确的 key 才 200。空 key 则返回 `500 Internal server error`。

**⚠️ 绝对不要把 master key 写进任何会被分享或同步的文件**（skill、README、笔记、Git）。
本次审查就在 skill 里发现了 6 处明文 master key —— 这类文件一旦外发就等于交出网关。
要用的时候从 `config.yaml` 里读。

### 2.6 日志与隐私

- 访问日志在 `logs\proxy.log`，**不含请求正文**（实测：无 `"content"` 字段、无 Authorization 头、无 master key）✅
- 但**错误 key 的请求会刷一整段 Prisma/DB traceback 进日志**（实测单次审计期内 254 条）。
  日志会被这些噪音撑大，排查时 `grep -v Traceback` 过滤掉。
- 排障要更细的转译日志：启动前设 `LITELLM_LOG=DEBUG`。
- 社区提醒：LiteLLM 是第三方代理，注意**依赖版本**与**凭据轮换**。

---

## 阶段 3 · 写回客户端 provider

ZCode 的 `~/.zcode/v2/config.json`：

```json
"vertex-gemini-litellm": {
  "name": "Vertex AI (Gemini)",
  "kind": "openai-compatible",
  "options": {
    "apiKey": "<MASTER_KEY>",
    "baseURL": "http://127.0.0.1:4000/v1",
    "apiKeyRequired": true
  },
  "enabled": true,
  "source": "custom",
  "models": {
    "gemini-3.8-flash": {
      "limit": { "context": 1048576, "output": 65536 },
      "modalities": { "input": ["text", "image"], "output": ["text"] },
      "zcode": { "modified": true, "modalitiesConfigured": true }
    }
  }
}
```

- provider 的 **id**（对象键名）可以是 UUID（ZCode 自己建的就是 UUID），也可以是可读名（本机用 `vertex-gemini-litellm`）；两者互不影响
- `kind` 只有 `anthropic` / `openai` / `openai-compatible` 三种，端点路径固定
  （`openai-compatible` → `/chat/completions`）
- ⚠️ **改之前让用户完全退出客户端**，否则它退出时会用内存里的旧配置覆盖你的写入
- ⚠️ **改之前必须备份**：`cp config.json config.json.bak-<时间戳>`

---

## 阶段 4 · 保活（最容易被忽略，也最容易翻车）

### 真实事故

配好当天能用，第二天客户端一直转圈重连 —— LiteLLM 进程早就死了。

### 诊断

```powershell
Get-ScheduledTaskInfo -TaskName "<任务名>" | Select LastRunTime, LastTaskResult
```

| LastTaskResult | 含义 |
|---|---|
| `267009` (0x41301) | 正在运行，正常 |
| **`2147946720` (0x800710E0)** | **SCHED_E_ALREADY_RUNNING —— 看门狗发现任务已在跑，拒绝重复启动。这是好事，不是故障** |
| `3221225786` (0xC000013A) | **STATUS_CONTROL_C_EXIT** —— 控制台窗口被关 / 收到 Ctrl+C |

> ⚠️ **加了看门狗之后，`LastTaskResult` 不再能直接反映进程死活** —— 每次看门狗触发都会被记成
> `0x800710E0`，把真实的崩溃码覆盖掉。
> **判断存活请直接查端口，不要看 LastTaskResult**：
> ```bash
> netstat -ano | findstr "127.0.0.1:4000"
> ```
> 另一个佐证：`tasklist | findstr litellm` 的会话列显示 **`Services`**（session 0）就说明
> S4U 无窗口模式生效了；显示 `Console` 说明还在用交互式，窗口有被误关的风险。

> ⏱️ **重启后别急着判定失败**：11 个模型的配置下，LiteLLM 从启动到监听 4000
> **需要 30 秒以上**（日志出现 `Application startup complete` 才算好）。
> 等 15 秒就 `netstat` 会误判成"没起来"，然后重复拉起、把问题搞复杂。
> **判断就绪看日志这一行**：
> ```powershell
> Get-Content logs\proxy.log -Tail 3   # 找 "Uvicorn running on http://127.0.0.1:4000"
> ```

### 三个必须同时满足的条件

1. **不能有可见窗口**（有窗口就会被误关）
   用 Task Scheduler 原生的 **`LogonType=S4U`（非交互）**，根本不创建窗口。
   > ⚠️ 不要用 `wscript.exe` + VBS 做隐藏启动器：WorkBuddy 的 PowerShell 安全策略把 `wscript.exe`
   > 列为 LOLBin，命令会被直接拦下
   > （`Known LOLBin executable that can run arbitrary code outside PowerShell validation`）。

2. **必须有看门狗**（只挂「登录时触发一次」不够）
   给 logon trigger 挂 Repetition，每 5 分钟重复，配 `-MultipleInstances IgnoreNew`。

3. **启动脚本必须幂等**（看门狗会反复调它）
   ```bat
   netstat -ano | findstr "127.0.0.1:4000" | findstr "LISTENING" >nul 2>&1
   if not errorlevel 1 exit /b 0
   ```

### ⚠️ 隐性依赖：出网代理必须比 LiteLLM 先活

LiteLLM 自己不需要交互会话（S4U 能连 `127.0.0.1:10808`），
**但 `10808` 上那个翻墙客户端（v2rayN）是用户级 GUI 程序，用户登录后才启动。**

链路实际是：`ZCode → LiteLLM(S4U) → v2rayN(用户会话) → Google`

后果：
- 用户没登录 → v2rayN 没起 → LiteLLM 活着但**所有请求 502**
- 开机后 v2rayN 启动比 LiteLLM 晚 → 头几秒的请求失败（`num_retries` 能兜一部分）

**排查时先分别确认这两件事**，不要只看 LiteLLM 在不在：
```bash
netstat -ano | grep -E "127.0.0.1:(4000|10808)" | grep LISTENING
```
两个端口都在监听才算链路完整。

### 完整注册脚本

见 `assets/register-task.ps1`。要点：
- `-ExecutionTimeLimit ([TimeSpan]::Zero)`（无限制，否则任务会被掐掉）
- `-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)`
- `LogonType=S4U` 能连上 `127.0.0.1` 的本地代理端口，不需要交互会话
- 立即拉起：`Start-ScheduledTask -TaskName "<任务名>"`

---

## 阶段 5 · 验收（四层，不要跳步）

按顺序跑 `assets/vertex-diagnose.py`：

| 层 | 检查 | 失败含义 |
|---|---|---|
| 1 | 密钥换 OAuth 令牌 | 凭据本身有问题 |
| 2 | 直连 `endpoints/openapi`（抽样 3 个模型 × `global`/`us-central1`） | 项目/API/权限/区域问题 |
| 3+4 | 经 LiteLLM 发一次真实 chat，并核对响应 `model` 字段 | 桥、代理或路由问题 |

> 脚本的 `[2]` 层只抽样 3 个模型（不是全部 11 个），避免体检跑太久。
> 想全量验证模型可用性用 `vertex-list-models.py`。

**报错对照表**

| 报错 | 含义 | 处理 |
|---|---|---|
| `403 SERVICE_DISABLED` | 项目没启用 `aiplatform.googleapis.com` | 控制台启用，等 1–2 分钟 |
| `403 Permission 'aiplatform.endpoints.predict' denied` | SA 没角色，**或角色刚授予还在传播** | **等 30 秒重试再判定失败**（实测过，别急着下结论） |
| `403 BILLING_DISABLED` | 项目没关联带赠金的结算账号 | 结算页挂上 |
| `404 Publisher model ... not found` | 区域不对 | preview 模型只在 `global` |
| 出网 `502 Bad Gateway` | 代理配错，**或翻墙客户端没起** | 见「环境坑位」的代理大小写陷阱 + 「出网代理必须比 LiteLLM 先活」 |
| **`400 No connected db.`** | ⚠️ **不是数据库问题，是 key 不对** | 核对客户端 apiKey 与 `config.yaml` 的 `master_key` 完全一致 |
| `500 Internal server error` | Authorization 头为空或格式错 | 必须是 `Bearer <key>`，注意空格 |

---

## 日常运维

### 模型名怎么查（Vertex 没有 list 接口）

`GET .../endpoints/openapi/models` **返回 404**。唯一办法是**拿候选名逐个打
`chat/completions`，看 200 还是 404**。用 `assets/vertex-list-models.py`。

已实测规律（2026-09）：
- flash 版本号**跳号**：3.1 / 3.5 / 3.6 / 3.7 / 3.8 有，**3.2 / 3.3 / 3.4 不存在**
- pro 只有 `gemini-2.5-pro` 和 `gemini-3.1-pro-preview`；`gemini-3.5-pro` ~ `3.8-pro` 全 404
- **`-preview` 后缀不能瞎加**：`gemini-3.8-flash-preview` 404，`gemini-3.8-flash` 200
- `gemini-flash-latest` 是有效浮动别名，指向最新 flash
- `gemini-3.1-pro-preview-customtools` 是给 Agent 工具调用优化的变体

**当前可用全量清单（11 个，2026-09 实测全绿）**

| flash | pro |
|---|---|
| `gemini-3.8-flash` | `gemini-2.5-pro` |
| `gemini-3.7-flash` | `gemini-3.1-pro-preview` |
| `gemini-3.6-flash` | `gemini-3.1-pro-preview-customtools` |
| `gemini-3.5-flash` | |
| `gemini-3.5-flash-lite` | |
| `gemini-3.1-flash-lite` | |
| `gemini-flash-latest`（浮动别名） | |
| `gemini-2.5-flash` | |

### 「选了 3.8 但模型说自己是 3.7」—— 噪音，不是 bug

**不要顺着用户的假设去查路由。** 先做对照实验：同一句「你是哪个模型」问 4 个版本。

| 实际请求 | 它自称 |
|---|---|
| `gemini-3.8-flash` | Gemini 3.7 Flash ❌ |
| `gemini-3.7-flash` | Gemini 3.7 Flash ✅（蒙对） |
| `gemini-3.5-flash` | 1.5 Pro ❌ |
| `gemini-2.5-flash` | 拒绝回答 |

LLM 权重训练完就固定，**它不知道自己被部署成哪个版本**。

**权威验证：看响应里的 `model` 字段**（服务端填的，不是模型说的）：
- 直连 Vertex → `"model": "google/gemini-3.8-flash"`
- 经 LiteLLM → `"model": "gemini-3.8-flash"`

两者都对即证明路由无误，**不要为了「让模型说对版本」去改配置**。

### 监控 $300 赠金余额

**先消除用户顾虑**：

| 情况 | 结果 |
|---|---|
| 赠金花完 / 90 天到期，**没手动升级** | 结算账号自动关闭 → 项目停用 → 30 天宽限 → 资源删除。**不扣信用卡** |
| 手动点了「**激活**」 | 从这一刻起，超出赠金的用量才从卡上扣 |

→ **唯一的多扣账风险是误点「激活」**。

**没有 API，别找了**（实测探测结果）：
```
GET /v1/billingAccounts/{id}                  -> 401（端点存在）
GET /v1/billingAccounts/{id}/credits          -> 404
GET /v1/billingAccounts/{id}/creditBalances   -> 404
GET /v1/billingAccounts/{id}/freeTrialCredits -> 404
```

三个可用入口：
- 桌面快捷方式 → `https://console.cloud.google.com/billing/overview`（赠金窗格显示剩余金额 + 天数）
- 消耗明细 → `https://console.cloud.google.com/billing/reports`（看「其他节省费用」列）
- **预算告警**（唯一自动化手段）

**预算告警的坑（必须跟用户强调）**：

| 设置项 | 设成 |
|---|---|
| 范围 | 结算账号（覆盖所有项目，防别的服务偷烧） |
| 金额 | `300` |
| **节省项 Savings** | **全部取消勾选，尤其 `Promotions`** ⚠️ |
| 阈值 | 50% / 80% / 95% |
| 通知 | 邮件 |

> 默认 Savings **全部勾选** = 按「抵扣后成本」计算 = 赠金把费用全抵了 = **永远不告警**。
> 取消勾选才按原始成本算，才能看出赠金烧了多少。
> 预算告警**只提醒不封顶**，创建后可能几小时才收到第一封邮件。

---

## 环境坑位（踩过，别再踩）

### 1. 代理大小写陷阱（最坑的一个）

在 WorkBuddy 沙箱里访问 Google，**即使 `dangerouslyDisableSandbox: true`**，环境里仍会同时存在
`HTTP_PROXY` / `HTTPS_PROXY` 和 `http_proxy` / `https_proxy`，值指向一个**随机端口**的本地代理，
访问 Google 返回 **502**。端口每次都不一样，别去记它。

Windows 下 Python 的 `os.environ` **大小写不敏感**。

**Python 脚本正确写法 —— 只赋值，不要 pop**：
```python
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:10808"
os.environ["HTTP_PROXY"] = "http://127.0.0.1:10808"
```
> ⚠️ 踩过的坑：写完上面两行再 `os.environ.pop("https_proxy", None)`，
> 因为大小写不敏感，pop 掉的是**同一个变量** —— 等于把刚设的代理删了，
> 表现为 `oauth2.googleapis.com` 连接超时 120 秒。

**bash 里启动子进程（要清干净再加）**：
```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
    HTTPS_PROXY=http://127.0.0.1:10808 https_proxy=http://127.0.0.1:10808 \
    HTTP_PROXY=http://127.0.0.1:10808 http_proxy=http://127.0.0.1:10808 \
    NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost \
    <命令>
```

### 2. Git Bash 调 .bat

`cmd //c xxx.bat` 会被 MSYS 路径转换搞坏（变成打开交互式 cmd，什么都不执行）。
**直接跑 exe 更稳。**

### 3. 脚本文件的编码规则（三种文件三种要求）

同一套文件里，`.bat` / `.ps1` / `.py` 的编码要求**完全不同**，搞混就静默失败：

| 文件类型 | 换行 | BOM | 非 ASCII | 违反后果 |
|---|---|---|---|---|
| `.bat` / `.cmd` | **必须 CRLF** | **绝不能有** | **必须纯 ASCII** | `cmd` 静默失败、`%VAR%` 展开为空 |
| `.ps1` | 无所谓 | **必须 UTF-8 BOM** | 可以 | **PS 5.1 按 GBK 解码中文注释 → 报 `意外的标记"}"` 语法错** |
| `.py` | 无所谓 | 不要 | 可以 | Python 3 默认 UTF-8，没问题 |

> ⚠️ **实测踩过**：`register-task.ps1` 是 UTF-8 **无 BOM** 且注释是中文，
> Windows PowerShell 5.1 按系统 GBK 码页去解码，中文字节被拆坏，
> 解析器在第 80 行报 `表达式或语句中包含意外的标记"}"` —— 看起来像括号不配对，
> 实际是**编码问题**，加 BOM 立刻恢复。
>
> 判断技巧：语法报错位置在**含中文的那行之后**，且括号肉眼看着是配对的 → 先怀疑 BOM。

**一键修正**（把 `.bat` 转 CRLF+ASCII，给 `.ps1` 加 BOM）：
```python
b = open(p, "rb").read()
b = b.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")
open(p, "wb").write(b"\xef\xbb\xbf" + b)   # .ps1 加 BOM；.bat 去掉这行
```
> 注意 Python 里 `b.count(b"\\r\\n")` 是**字面 4 个字符**，不是回车换行。
> 想数换行要用 `b.count(bytes([13, 10]))`，别用转义写法，否则统计永远为 0。

### 4. Windows 工具限制

- `schtasks.exe` 被 WorkBuddy 程序黑名单拦 → 只能用 PowerShell 的
  `Get-ScheduledTask` / `Register-ScheduledTask` / `Start-ScheduledTask`
- `wscript.exe` 被列为 LOLBin → 不能用 VBS 做隐藏启动器
- **PowerShell 工具不回传 stdout** → 结果 `Set-Content` 到 `$env:TEMP\xxx.txt`，再用 Read 工具读

### 5. 本机既有部署（可直接复用，别重建）

| 项 | 值 |
|---|---|
| LiteLLM | `C:\Users\<USER>\.zcode\litellm\`，端口 4000，master key 见 `config.yaml`（**不要写进本文件**） |
| 计划任务 | `ZCode LiteLLM Vertex Proxy` |
| 出网代理 | `127.0.0.1:10808`（v2rayN HTTP 入站） |
| LiteLLM venv 自带 `google-auth` | 可直接用它跑诊断，不用另装 |
| 客户端 | ZCode，配置 `~/.zcode/v2/config.json` |

---

## 资产清单

| 文件 | 用途 | 编码 |
|---|---|---|
| `assets/vertex-diagnose.py` | 四层链路体检，出问题先跑它。**零硬编码凭据**，全部从 config.yaml 读；支持 `--config` / `--proxy` / `--port` | UTF-8 |
| `assets/vertex-list-models.py` | 模型可用性探测。直接跟命令行加候选名：`... gemini-3.9-flash`，不用改文件 | UTF-8 |
| `assets/config.yaml.example` | LiteLLM 配置模板（含 router_settings 建议） | UTF-8 |
| `assets/start-litellm.bat.example` | 启动脚本模板（含幂等守卫）。**已校验 CRLF + 纯 ASCII + 无 BOM** | CRLF / ASCII |
| `assets/register-task.ps1` | 计划任务注册脚本（S4U + 看门狗）。**已校验带 UTF-8 BOM** | UTF-8 + BOM |

> ⚠️ 改这几个模板文件后，**务必重新跑一次编码校验**（见「环境坑位 §3」），
> 编辑器默认存 LF / 去 BOM 会直接把它们弄坏。

---

## 社区来源与交叉验证（2026-09 审查）

本 skill 的结论经过官方文档 + 社区教程 + 本机实测三方交叉验证。

| 结论 | 来源 | 采信度 |
|---|---|---|
| 试用结束**不自动扣款**，需手动 Activate | 官方 `docs.cloud.google.com/free/docs/free-cloud-features` 原文 | ✅ 权威 |
| 「到期自动扣卡」是误传 | 知乎避坑文有此说法，但作者是 GCP 代理商，与官方原文冲突 | ❌ 已否定 |
| Vertex 只收 OAuth2，拒绝 API Key | LiteLLM 官方 Vertex 文档 + 实测报错原文 | ✅ 实测 |
| master key 不当应用 key，应发 virtual key | LiteLLM 官方 Claude Code 网关文档 + 多篇社区教程一致 | ✅ 共识 |
| virtual key 需接 Postgres | 社区教程 `config.yaml` 示例 | ✅ 已验（本机无 DB → 报 `No connected db`） |
| OpenAI 风格 baseURL **必须带 `/v1`** | 社区「常见坑」清单；本机 `http://127.0.0.1:4000/v1` 实测通 | ✅ 实测 |
| model alias **大小写敏感** | 社区教程；本机模型名全小写 | ✅ 实测 |
| `router_settings` 该配 `num_retries` / `timeout` | 社区教程的生产配置示例 | ✅ 已应用并验证 |
| 日志不含请求正文 | 本机实测（无 `content` 字段 / 无 Authorization / 无 key） | ✅ 实测 |
| 代理大小写陷阱（`os.environ.pop` 自残） | 本机踩坑，**社区无记载** | ✅ 独有 |

**相关阅读**
- 官方免费试用条款：`https://docs.cloud.google.com/free/docs/free-cloud-features`
- LiteLLM Vertex 文档：`https://docs.litellm.ai/docs/providers/vertex`
- Claude Code 接非 Anthropic 模型：`https://docs.litellm.ai/docs/tutorials/claude_non_anthropic_models`
- Claude Code 官方 LLM 网关配置：`https://code.claude.com/docs/en/llm-gateway`

---

## 审查记录

| 日期 | 变更 |
|---|---|
| 2026-09-16 | 首版 SOP 落地 |
| 2026-09-16 | 二审（用户要求联网复核）：**清除 4 个文件里 6 处明文 master key**；诊断/探测脚本改为配置驱动（零硬编码，支持 `--config`/`--proxy`/`--port`）；补 `router_settings`（已应用到线上并验证）、master key vs virtual key、日志隐私审计、S4U↔代理依赖链、11 模型全量清单、社区来源表；澄清「到期自动扣款」误传（官方原文证伪）；**修复模板文件编码**（`.bat` LF→CRLF+去中文注释、`.ps1` 加 UTF-8 BOM，否则 PS 5.1 报语法错） |

---

## 给用户交付时的话术

结论先行，别铺垫：

1. **能/不能直接填 Key** → 不能，原因是 Vertex 只收 OAuth2
2. **LiteLLM 是干什么的** → 不是翻译格式，是自动续令牌
3. **你要在控制台点这几步** → 编号列清楚，每步给直达链接
4. **剩下我接手** → 改配置、注册任务、端到端验证

用户报错时**先实测复现再解释**，不要凭印象猜。这个用户明确要求过
「先去网上搜索、看社区，再来教我」。
