# vertex-ai-litellm-bridge

把 **Google Cloud $300 赠金 → Vertex AI (Gemini)** 接进 **ZCode / Claude Code / OpenCode / Cline / Cursor** 这类只认
`Anthropic Messages` / `Chat Completions` / `Responses` 三种协议的客户端。

> 一份实测过的完整 SOP + 可直接复用的诊断脚本，不是"教程搬运"。

---

## 为什么需要这个

你拿着一张 GCP $300 赠金，想用里面的 Gemini，结果会撞上三堵墙：

| 墙 | 现实 |
|---|---|
| 想「填个 API Key + Base URL」就完事？ | **不行。** Vertex 的 OpenAI 兼容端点只收 OAuth2 令牌，明确拒绝 API Key（`API keys are not supported by this API. Expected OAuth2 access token`） |
| 建服务账号密钥？ | **被组织政策拦。** 2024-05-03 后创建的 GCP 组织默认强启 `iam.disableServiceAccountKeyCreation` |
| 那 LiteLLM 是干嘛的？ | **不是翻译格式**，而是「拿服务账号 JSON 自动换 OAuth 令牌 + 每小时自动续 + 对外给一个固定地址和固定 Key」 |

这个仓库把这三堵墙怎么绕、怎么验证、怎么保活，全部写清楚了。

---

## 三十秒结论

1. **不能直接填 Key** —— Vertex 只收 OAuth2
2. **需要一层本地网关** —— LiteLLM 负责自动续令牌
3. **控制台要用户自己点 6 步** —— 其余全自动
4. **不升级为付费账号就不会扣卡** —— 赠金到期 = 结算账号关闭，不是自动转付费

---

## 目录结构

```
SKILL.md                        完整 SOP（按执行顺序：拿凭据 → 搭桥 → 写回客户端 → 保活 → 验收）
assets/
├── vertex-diagnose.py          四层链路体检，出问题先跑它
├── vertex-list-models.py       模型可用性探测（Vertex 没有 list 接口，只能逐个试）
├── config.yaml.example         LiteLLM 配置模板
├── start-litellm.bat.example   启动脚本模板（含幂等端口守卫）
└── register-task.ps1           计划任务注册（S4U 无窗口 + 看门狗）
```

---

## 快速开始

```bash
# 1) 体检当前链路（零硬编码凭据，全部从 config.yaml 读）
python assets/vertex-diagnose.py

# 换配置位置 / 代理端口
python assets/vertex-diagnose.py --config D:\x\config.yaml --proxy http://127.0.0.1:7890

# 2) 试新模型能不能用（直接跟命令行加，不用改文件）
python assets/vertex-list-models.py gemini-3.9-flash gemini-4-flash
```

四层检查依次是：**密钥换令牌 → 直连 Vertex → 本地网关存活 → 真实转发**。

---

## 这个 SOP 覆盖的坑

都是实测踩出来的，社区教程里没有：

- **`os.environ.pop("https_proxy")` 会删掉刚设的 `HTTPS_PROXY`** —— Windows 环境变量大小写不敏感，表现为 Google 连接超时 120 秒
- **`400 No connected db.` 其实是 key 错的伪装** —— 不是数据库问题。key 多一位/少一位/乱编全返回这个 400
- **加了看门狗后 `LastTaskResult` 不再反映进程死活** —— 每次触发都被记成 `0x800710E0`，把真实崩溃码覆盖掉，只能查端口
- **`.ps1` 无 BOM + 中文注释 → PowerShell 5.1 报 `意外的标记"}"`** —— 看着像括号不配对，实际是 GBK 误解码
- **翻墙代理是用户级 GUI，S4U 任务比它先起来** —— 网关活着但全部 502，排查要看两个端口
- **LLM 不知道自己被部署成哪个版本** —— 「选了 3.8 却自称 3.7」是噪音，权威验证看响应的 `model` 字段
- **赠金到期不会自动扣款** —— 官方原文已核，社区流传的「会自动扣卡」多来自代理商导流文

---

## 安全说明

- 本仓库**不含任何凭据**：master key、服务账号私钥、项目 ID 全部用占位符（`<MASTER_KEY>` / `<PROJECT_ID>`）
- 诊断脚本**零硬编码**，运行时从你的 `config.yaml` 读取
- `master_key` 是网关的 root 凭证 —— **绝不要提交进 Git**

---

## 环境

主要面向 **Windows**（计划任务保活、`.bat`/`.ps1` 编码、S4U 无窗口都针对 Windows 写）。
LiteLLM 本身跨平台，Linux/macOS 下换成 systemd / launchd 即可，SKILL.md 阶段 4 的思路通用。

---

## License

MIT
