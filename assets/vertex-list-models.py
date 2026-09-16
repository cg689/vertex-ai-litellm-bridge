"""
Vertex AI 模型可用性探测

Vertex 没有「列出模型」接口（GET .../endpoints/openapi/models 返回 404），
唯一办法是拿候选名逐个打 chat/completions，看 200 还是 404。

用法（必须绕开 WorkBuddy 沙箱注入的代理，否则一律超时）：
    C:\\Users\\<USER>\\.zcode\\litellm\\venv\\Scripts\\python.exe vertex-list-models.py

    # 试新模型：直接用命令行加，不用改文件
    ... vertex-list-models.py gemini-3.9-flash gemini-4-flash
    # 换配置位置 / 代理端口
    ... vertex-list-models.py --config D:\\x\\config.yaml --proxy http://127.0.0.1:7890

项目 ID、密钥路径、区域全部从 LiteLLM 的 config.yaml 运行时读取。
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_CFG = os.path.join(os.path.expanduser("~"), ".zcode", "litellm", "config.yaml")

# 没有传命令行参数时，探测这份清单
DEFAULT_CANDIDATES = [
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
    "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-2.5-flash", "gemini-2.5-pro",
    "gemini-3.1-pro-preview", "gemini-3.1-pro-preview-customtools",
    # 新版本出来后加到命令行即可，例如：
    #   vertex-list-models.py gemini-3.9-flash
]

ap = argparse.ArgumentParser()
ap.add_argument("--config", default=DEFAULT_CFG, help="LiteLLM config.yaml 路径")
ap.add_argument("--proxy", default="http://127.0.0.1:10808", help="出网代理")
ap.add_argument("candidates", nargs="*", help="要探测的模型名（留空用内置清单）")
args = ap.parse_args()

if not os.path.exists(args.config):
    sys.exit(f"找不到 config.yaml: {args.config}")

cfg = open(args.config, encoding="utf-8").read()


def pick(pattern, default=""):
    m = re.search(pattern, cfg, re.M)
    return m.group(1).strip() if m else default


PROJECT = pick(r"vertex_project:\s*(\S+)")
KEY = pick(r"vertex_credentials:\s*(.+?)\s*$")
LOCATION = pick(r"vertex_location:\s*(\S+)", "global")
if not PROJECT or not KEY:
    sys.exit("config.yaml 里没解析到 vertex_project / vertex_credentials。")

LOCATIONS = list(dict.fromkeys([LOCATION, "us-central1"]))
CANDIDATES = args.candidates or DEFAULT_CANDIDATES

# ⚠️ 只赋值，不要 pop —— Windows 下 os.environ 大小写不敏感
os.environ["HTTPS_PROXY"] = args.proxy
os.environ["HTTP_PROXY"] = args.proxy

from google.oauth2 import service_account          # noqa: E402
import google.auth.transport.requests              # noqa: E402

creds = service_account.Credentials.from_service_account_file(
    KEY, scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(google.auth.transport.requests.Request())
TOKEN = creds.token

print(f"project : {PROJECT}")
print(f"待测模型: {len(CANDIDATES)} 个，区域: {', '.join(LOCATIONS)}\n")


def probe(loc, model):
    url = (f"https://aiplatform.googleapis.com/v1/projects/{PROJECT}"
           f"/locations/{loc}/endpoints/openapi/chat/completions")
    body = json.dumps({"model": f"google/{model}",
                       "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 8}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return 200, ""
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            msg = json.loads(raw)["error"]["message"]
        except Exception:
            msg = raw[:100]
        return e.code, msg[:70]
    except Exception as e:
        return "ERR", type(e).__name__


print(f"{'model':40s} " + "  ".join(f"{l:12s}" for l in LOCATIONS))
print("-" * 74)
available = []
for m in CANDIDATES:
    cells = []
    for loc in LOCATIONS:
        code, msg = probe(loc, m)
        cells.append("200 OK" if code == 200 else f"{code}")
        if code == 200 and m not in available:
            available.append(m)
    print(f"{m:40s} " + "  ".join(f"{c:12s}" for c in cells))

print("\n当前可用（可直接写进 config.yaml 的 model_name）：")
for m in available:
    print("  -", m)
if not available:
    print("  （无）检查项目/区域/权限")
