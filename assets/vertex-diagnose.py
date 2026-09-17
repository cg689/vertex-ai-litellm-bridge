"""
Vertex AI 链路体检脚本

用法（必须绕开 WorkBuddy 沙箱注入的代理，否则一律 502）：
    C:\\Users\\<USER>\\.zcode\\litellm\\venv\\Scripts\\python.exe vertex-diagnose.py

    # 非默认位置时用参数覆盖
    ... vertex-diagnose.py --config D:\\x\\config.yaml --proxy http://127.0.0.1:7897

检查 4 层：
  1. 服务账号密钥能否换到 OAuth 令牌
  2. 直连 Vertex AI OpenAI 兼容端点（global / us-central1）
  3. 本地 LiteLLM 代理是否存活
  4. 经 LiteLLM 转发是否通

配置（项目 ID / 密钥路径 / master key / 模型清单）全部从 LiteLLM 的
config.yaml 运行时读取，不硬编码任何凭据。
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

# ---------- 定位 config.yaml ----------
DEFAULT_CFG = os.path.join(os.path.expanduser("~"), ".zcode", "litellm", "config.yaml")

_ap = argparse.ArgumentParser(add_help=True)
_ap.add_argument("--config", default=DEFAULT_CFG, help="LiteLLM config.yaml 路径")
_ap.add_argument("--proxy", default="http://127.0.0.1:7897",
                 help="出网代理（本机翻墙代理的 HTTP 入站端口；2026-09-16 起为 Clash Verge Rev 的 7897）")
_ap.add_argument("--port", type=int, default=4000, help="LiteLLM 监听端口")
_args = _ap.parse_args()

if not os.path.exists(_args.config):
    sys.exit(f"找不到 config.yaml: {_args.config}\n用 --config 指定实际路径。")

_cfg = open(_args.config, encoding="utf-8").read()


def _pick(pattern, default=""):
    """按行匹配（re.M），返回第一个捕获组；匹配不到返回 default。"""
    m = re.search(pattern, _cfg, re.M)
    return m.group(1).strip() if m else default


MASTER_KEY = _pick(r"master_key:\s*(\S+)")
KEY = _pick(r"vertex_credentials:\s*(.+?)\s*$")
PROJECT = _pick(r"vertex_project:\s*(\S+)")
# 从 model_list 里按出现顺序取出全部 model_name，去重
MODELS = list(dict.fromkeys(re.findall(r"model_name:\s*(\S+)", _cfg)))

PROXY = _args.proxy
LITELLM = f"http://127.0.0.1:{_args.port}"

# ⚠️ 只赋值，不要 pop —— Windows 下 os.environ 大小写不敏感，
#    pop("https_proxy") 会把刚设的 HTTPS_PROXY 一起删掉。
os.environ["HTTPS_PROXY"] = PROXY
os.environ["HTTP_PROXY"] = PROXY
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

LOCATION = _pick(r"vertex_location:\s*(\S+)", "global")

print(f"config  : {_args.config}")
print(f"project : {PROJECT}    location: {LOCATION}")
print(f"模型数  : {len(MODELS)}  -> {', '.join(MODELS[:4])}{' ...' if len(MODELS) > 4 else ''}")
print(f"出网代理: {PROXY}")
if not MODELS:
    sys.exit("config.yaml 里没解析到 model_name，检查缩进格式。")
if not KEY:
    sys.exit("config.yaml 里没解析到 vertex_credentials 路径。")
if not MASTER_KEY:
    sys.exit("config.yaml 里没解析到 master_key。")
# 第 2 层只抽 3 个样本模型打直连，避免 11 个模型 × 2 区域拖慢体检
SAMPLE = MODELS[:3]


def post(url, body, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return "ERR", f"{type(e).__name__}: {e}"


print("=" * 60)
print("[1] 服务账号密钥 -> OAuth 令牌")
try:
    from google.oauth2 import service_account
    import google.auth.transport.requests

    creds = service_account.Credentials.from_service_account_file(
        KEY, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    print(f"    OK  {creds.service_account_email}  token={len(creds.token)}B")
    token = creds.token
except Exception as e:
    print(f"    FAIL  {type(e).__name__}: {e}")
    raise SystemExit(1)

print("=" * 60)
print("[2] 直连 Vertex AI（抽样，验证项目/API/权限/区域）")
for loc in [LOCATION, "us-central1"]:
    for m in SAMPLE:
        url = (f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}"
               f"/locations/{loc}/endpoints/openapi/chat/completions")
        code, txt = post(url, {"model": f"google/{m}",
                               "messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 8},
                         {"Authorization": f"Bearer {token}",
                          "Content-Type": "application/json"})
        if code == 200:
            print(f"    [{loc:11s}] {m:26s} 200")
        else:
            try:
                msg = json.loads(txt)["error"]["message"][:90]
            except Exception:
                msg = str(txt)[:90]
            print(f"    [{loc:11s}] {m:26s} {code}  {msg}")
            if "SERVICE_DISABLED" in txt:
                print("       -> 项目没启用 aiplatform.googleapis.com")
            elif "predict' denied" in txt or "PERMISSION_DENIED" in txt:
                print("       -> SA 没角色，或角色刚授予还在传播：等 30 秒重试再判定")
            elif "BILLING_DISABLED" in txt:
                print("       -> 项目没关联带赠金的结算账号")
            elif "not found" in txt.lower():
                print("       -> 区域不对：preview 模型只在 global")

print("=" * 60)
print("[3+4] 本地 LiteLLM：存活 + 真实转发")
PROBE = MODELS[0]
code, txt = post(LITELLM + "/v1/chat/completions",
                 {"model": PROBE,
                  "messages": [{"role": "user", "content": "say OK"}],
                  "max_tokens": 64},
                 {"Authorization": f"Bearer {MASTER_KEY}",
                  "Content-Type": "application/json"})
if code == 200:
    d = json.loads(txt)
    # 权威验证：看服务端回的 model 字段，而不是问模型「你是谁」
    print(f"    OK  请求 {PROBE} -> 响应 model = {d.get('model')!r}")
    print("    usage:", d.get("usage"))
else:
    print(f"    FAIL {code}  {str(txt)[:300]}")
    print("    -> ERR/超时 : 代理没启动，或出网代理端口不对")
    print("    -> 400 'No connected db' : **key 不对**（不是数据库问题，见 SKILL.md 说明）")
    print("    -> 500 : Authorization 头为空或格式错（要 'Bearer <key>'）")
    print("    -> 502 : LiteLLM 连不上 Google，检查出网代理")
