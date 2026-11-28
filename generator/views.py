import base64
import binascii
import io
import json
import logging
import os
import queue
import re
import secrets
import textwrap
import threading
import time
import urllib.request
import zipfile

from django.db import OperationalError, close_old_connections, transaction
from django.db.models import Max
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import GenerateHistory, GenerateVersion, GenerationJob, PublishedPage
from .scoring import score_html

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是前端工程师，只输出完整可独立运行的单文件HTML，"
    "里面JS可以交互，不要额外解释、不要markdown代码块标记。"
)

MAX_PROMPT_LENGTH = 3000
MAX_INSTRUCTION_LENGTH = 1000
MAX_LLM_RESPONSE_BYTES = 2 * 1024 * 1024
REFERENCE_IMAGE_MAX_BYTES = 2_500_000
REFERENCE_IMAGE_MAX_CHARS = 3_500_000

PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "LLM_API_KEY",
        "base_env": "LLM_BASE_URL",
        "default_base": "https://api.deepseek.com",
        "model_env": "LLM_MODEL",
        "default_model": "deepseek-chat",
        "timeout_env": "LLM_TIMEOUT",
        "default_timeout": 30,
    },
    "qwen": {
        "label": "千问",
        "key_env": "QWEN_API_KEY",
        "base_env": "QWEN_BASE_URL",
        "default_base": "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1",
        "model_env": "QWEN_MODEL",
        "default_model": "qwen3.6-flash",
        "timeout_env": "QWEN_TIMEOUT",
        "default_timeout": 60,
    },
}
RACE_PROVIDERS = ("deepseek", "qwen")

MOCK_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>加法计算器 · Mock 预览</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
      background: linear-gradient(135deg, #eef2ff 0%, #f8fafc 48%, #ecfeff 100%);
      color: #0f172a; padding: 24px;
    }
    .card {
      width: min(440px, 100%); padding: 32px; border-radius: 24px;
      background: rgba(255,255,255,.9); border: 1px solid rgba(148,163,184,.35);
      box-shadow: 0 24px 70px rgba(30,41,59,.14); backdrop-filter: blur(12px);
    }
    .eyebrow {
      display: inline-flex; align-items: center; gap: 8px; margin-bottom: 18px;
      font-size: 12px; font-weight: 700; letter-spacing: .08em; color: #4f46e5;
      text-transform: uppercase;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #6366f1; }
    h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: -.03em; }
    p.sub { margin: 0 0 26px; color: #64748b; font-size: 14px; line-height: 1.6; }
    .numbers { display: grid; grid-template-columns: 1fr auto 1fr; gap: 12px; align-items: end; }
    label { display: block; margin-bottom: 8px; font-size: 13px; font-weight: 600; color: #475569; }
    input {
      width: 100%; padding: 14px 16px; font-size: 20px; color: #0f172a;
      border: 1px solid #cbd5e1; border-radius: 13px; outline: none;
      background: #fff; transition: border-color .2s, box-shadow .2s;
    }
    input:focus { border-color: #6366f1; box-shadow: 0 0 0 4px rgba(99,102,241,.12); }
    .plus { padding-bottom: 15px; font-size: 26px; color: #94a3b8; }
    .actions { display: grid; grid-template-columns: 1fr auto; gap: 10px; margin-top: 22px; }
    button {
      border: 0; border-radius: 13px; padding: 14px 20px; font-size: 15px;
      font-weight: 700; cursor: pointer; transition: transform .15s, opacity .15s;
    }
    button:active { transform: translateY(1px); }
    #add {
      color: #fff; background: linear-gradient(135deg, #4f46e5, #0891b2);
      box-shadow: 0 10px 24px rgba(79,70,229,.24);
    }
    #reset { color: #475569; background: #f1f5f9; }
    .result {
      margin-top: 24px; padding: 18px 20px; border-radius: 16px;
      background: #0f172a; color: #e2e8f0; display: flex;
      align-items: baseline; justify-content: space-between; gap: 16px;
    }
    .result span:first-child { font-size: 13px; color: #94a3b8; }
    #output { font-size: 32px; font-weight: 800; color: #a5b4fc; letter-spacing: -.03em; }
    .hint { margin: 16px 0 0; font-size: 12px; color: #94a3b8; text-align: center; }
    @media (max-width: 420px) {
      .card { padding: 24px; } h1 { font-size: 24px; }
      .numbers { grid-template-columns: 1fr; } .plus { padding: 0; text-align: center; }
    }
  </style>
</head>
<body>
  <main class="card">
    <div class="eyebrow"><span class="dot"></span> Mock fallback</div>
    <h1>加法计算器</h1>
    <p class="sub">这是大模型不可用时自动生成的兜底页面，功能完整、可正常交互。</p>
    <div class="numbers">
      <div>
        <label for="a">第一个数</label>
        <input id="a" type="number" value="12" step="any">
      </div>
      <div class="plus">+</div>
      <div>
        <label for="b">第二个数</label>
        <input id="b" type="number" value="30" step="any">
      </div>
    </div>
    <div class="actions">
      <button id="add" type="button">计算结果</button>
      <button id="reset" type="button">重置</button>
    </div>
    <div class="result"><span>计算结果</span><span id="output">42</span></div>
    <p class="hint">按 Enter 也可计算</p>
  </main>
  <script>
    const a = document.getElementById('a');
    const b = document.getElementById('b');
    const output = document.getElementById('output');
    function calculate() {
      const left = Number(a.value);
      const right = Number(b.value);
      output.textContent = Number.isFinite(left + right) ? String(left + right) : '请输入数字';
    }
    document.getElementById('add').addEventListener('click', calculate);
    document.getElementById('reset').addEventListener('click', () => {
      a.value = '12'; b.value = '30'; output.textContent = '42'; a.focus();
    });
    [a, b].forEach((input) => input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') calculate();
    }));
  </script>
</body>
</html>"""


def _mock_result() -> tuple[str, bool]:
    return MOCK_HTML, True


def _extract_html(raw: str) -> str | None:
    """Remove markdown fences/prose that models sometimes add despite the prompt."""
    if not raw or not raw.strip():
        return None

    text = raw.strip()
    fenced = re.search(r"```(?:html)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    lower = text.lower()
    start = lower.find("<!doctype html")
    if start == -1:
        start = lower.find("<html")
    if start != -1:
        end = lower.rfind("</html>")
        text = text[start : end + len("</html>")] if end > start else text[start:]

    text = text.strip()
    return text if "<" in text else None


def _looks_like_jsx(body: str) -> bool:
    """Detect real JSX instead of ordinary DOM code.

    Vanilla scripts often call ``document.createElement`` and embed HTML in
    template strings.  Treating those as JSX used to convert the script to
    ``text/babel``; Babel then executed it after DOMContentLoaded had already
    fired, so listeners such as the fade-in observer and mobile menu never ran
    and the preview looked blank/incomplete.  Require both a React runtime
    signal and an actual JSX marker before switching to Babel.
    """
    lower = body.lower()
    has_react = (
        "reactdom" in lower
        or "react." in lower
        or 'from "react"' in lower
        or "from 'react'" in lower
        or 'require("react")' in lower
        or "require('react')" in lower
    )
    if not has_react:
        return False

    markers = (
        r"</?[A-Z][A-Za-z0-9]*(?:\s|/?>)|</>|<>",
        r"(?:return|=>)\s*\(?\s*<[A-Za-z][A-Za-z0-9]*(?:\s|/?>)",
        r"\bclassName\s*=\s*[{\"]",
        r"\bon[A-Z][A-Za-z]+\s*=\s*\{",
        r"\bstyle\s*=\s*\{\{",
        r"\{\s*[A-Za-z_$][\w$]*(?:\.[\w$]+)*\s*&&\s*<",
        r"\?\s*<[A-Za-z][A-Za-z0-9]*(?:\s|/?>)",
    )
    return any(re.search(pattern, body) for pattern in markers)


def _ensure_runnable_html(html_code: str) -> str:
    """React pages produced as JSX need Babel in a no-build preview.

    Many models correctly choose React but forget that browsers cannot parse
    JSX. Injecting the standalone transformer keeps those previews runnable
    instead of showing a blank page caused by an inline SyntaxError.
    """
    if not html_code:
        return html_code

    converted = False
    script_pattern = re.compile(
        r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def convert(match: re.Match) -> str:
        nonlocal converted
        attrs = match.group("attrs")
        if re.search(r"\bsrc\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        body = match.group("body")
        if not _looks_like_jsx(body):
            return match.group(0)
        attrs = re.sub(
            r"\s+type\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
            "",
            attrs,
            flags=re.IGNORECASE,
        )
        converted = True
        return f'<script type="text/babel"{attrs}>{body}</script>'

    patched = script_pattern.sub(convert, html_code)
    if not converted:
        return html_code

    lower = patched.lower()
    if "babel/standalone" in lower or "babel.min.js" in lower:
        return patched

    if "cdn.jsdelivr.net" in lower:
        babel_url = "https://cdn.jsdelivr.net/npm/@babel/standalone/babel.min.js"
    else:
        babel_url = "https://unpkg.com/@babel/standalone/babel.min.js"
    babel_tag = f'<script src="{babel_url}"></script>\n'

    head_end = lower.find("</head>")
    if head_end != -1:
        return patched[:head_end] + babel_tag + patched[head_end:]

    marker = re.search(r"<script\s+type=\"text/babel\"", patched, flags=re.IGNORECASE)
    if marker:
        return patched[:marker.start()] + babel_tag + patched[marker.start():]
    return babel_tag + patched


def _repair_babel_preview(html_code: str) -> str:
    """Un-wrap scripts that older heuristics wrongly converted to Babel.

    A vanilla DOM script containing ``createElement`` and HTML template strings
    was once marked as JSX.  Babel then ran it after DOMContentLoaded, so page
    handlers did not attach and fade-in sections stayed invisible.  Repair such
    stored previews on output while keeping real JSX pages untouched.
    """
    if not html_code or "text/babel" not in html_code.lower():
        return html_code

    script_pattern = re.compile(
        r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    remaining_babel = False

    def repair(match: re.Match) -> str:
        nonlocal remaining_babel
        attrs = match.group("attrs")
        if re.search(r"\bsrc\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        if not re.search(
            r"""\btype\s*=\s*["']text/babel["']""", attrs, flags=re.IGNORECASE
        ):
            return match.group(0)
        body = match.group("body")
        if _looks_like_jsx(body):
            remaining_babel = True
            return match.group(0)
        attrs = re.sub(
            r"""\s+type\s*=\s*["']text/babel["']""",
            "",
            attrs,
            flags=re.IGNORECASE,
        )
        return f"<script{attrs}>{body}</script>"

    patched = script_pattern.sub(repair, html_code)
    if not remaining_babel:
        patched = re.sub(
            r"""<script[^>]*\bsrc\s*=\s*["'][^"']*babel[^"']*["'][^>]*>\s*</script>\s*""",
            "",
            patched,
            flags=re.IGNORECASE,
        )
    return patched


def _provider_is_configured(provider: str) -> bool:
    return bool(os.getenv(PROVIDERS[provider]["key_env"], "").strip())


def _provider_model(provider: str) -> str:
    config = PROVIDERS[provider]
    return os.getenv(config["model_env"], config["default_model"]).strip()


def _provider_timeout(provider: str) -> float:
    config = PROVIDERS[provider]
    try:
        return float(os.getenv(config["timeout_env"], str(config["default_timeout"])))
    except ValueError:
        return float(config["default_timeout"])


def _provider_endpoint(provider: str) -> str:
    config = PROVIDERS[provider]
    base_url = os.getenv(config["base_env"], config["default_base"]).strip().rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"


def _call_model(provider: str, user_prompt: str) -> tuple[str, bool]:
    """Call one OpenAI-compatible model. Any failure returns Mock HTML."""
    config = PROVIDERS[provider]
    label = config["label"]
    api_key = os.getenv(config["key_env"], "").strip()
    if not api_key:
        logger.info("%s API key is empty; using mock HTML.", label)
        return _mock_result()

    endpoint = _provider_endpoint(provider)
    model = _provider_model(provider)
    timeout = _provider_timeout(provider)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_LLM_RESPONSE_BYTES)
        data = json.loads(body.decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        html = _extract_html(content)
        if not html:
            logger.warning("%s returned empty/invalid HTML; using mock HTML.", label)
            return _mock_result()
        html = _ensure_runnable_html(html)
        logger.info(
            "%s generated %s chars in %.1fs.", label, len(html),
            time.monotonic() - started_at,
        )
        return html, False
    except Exception as exc:
        logger.warning(
            "%s request failed after %.1fs; using mock HTML: %s",
            label, time.monotonic() - started_at, exc,
        )
        return _mock_result()


def _stream_model(provider: str, user_prompt: str, emit) -> dict:
    """Stream one model through the OpenAI-compatible SSE protocol."""
    config = PROVIDERS[provider]
    label = config["label"]
    model = _provider_model(provider)
    api_key = os.getenv(config["key_env"], "").strip()

    def event(status: str, message: str = "", **extra) -> None:
        emit(
            {
                "type": "provider",
                "provider": provider,
                "label": label,
                "model": model,
                "status": status,
                "message": message,
                **extra,
            }
        )

    if not api_key:
        logger.info("%s API key is empty; using mock HTML.", label)
        event("fallback", "未配置 API Key")
        return {"provider": provider, "html_code": MOCK_HTML, "is_mock": True}

    timeout = _provider_timeout(provider)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
        "stream": True,
    }
    request = urllib.request.Request(
        _provider_endpoint(provider),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    started_at = time.monotonic()
    deadline = started_at + timeout
    parts: list[str] = []
    buffered: list[str] = []
    buffer_chars = 0
    total_chars = 0
    last_emit = started_at
    streaming_announced = False

    def flush(force: bool = False) -> None:
        nonlocal buffer_chars, last_emit
        if not buffered:
            return
        now = time.monotonic()
        if force or buffer_chars >= 80 or now - last_emit >= 0.12:
            chunk = "".join(buffered)
            emit(
                {
                    "type": "delta",
                    "provider": provider,
                    "label": label,
                    "chars": len(chunk),
                }
            )
            buffered.clear()
            buffer_chars = 0
            last_emit = now

    event("connecting", f"已提交给 {model}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{label} streaming exceeded {timeout:g}s")
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_line = line[5:].strip()
                if data_line == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_line)
                    choices = chunk.get("choices") or []
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                except (json.JSONDecodeError, IndexError, AttributeError):
                    continue
                if not delta:
                    continue
                if not streaming_announced:
                    streaming_announced = True
                    event("streaming", "页面代码正在输出")
                parts.append(delta)
                buffered.append(delta)
                total_chars += len(delta)
                buffer_chars += len(delta)
                if total_chars > MAX_LLM_RESPONSE_BYTES:
                    raise ValueError(f"{label} response exceeded size limit")
                flush()
        flush(force=True)
        html = _extract_html("".join(parts))
        if not html:
            logger.warning("%s returned empty/invalid HTML; using mock HTML.", label)
            event("fallback", "模型返回内容无法解析为 HTML")
            return {"provider": provider, "html_code": MOCK_HTML, "is_mock": True}
        html = _ensure_runnable_html(html)
        elapsed = time.monotonic() - started_at
        logger.info("%s streamed %s chars in %.1fs.", label, len(html), elapsed)
        event("done", f"生成完成 · {len(html):,} 字符", chars=len(html))
        return {"provider": provider, "html_code": html, "is_mock": False}
    except Exception as exc:
        flush(force=True)
        elapsed = time.monotonic() - started_at
        logger.warning(
            "%s streaming failed after %.1fs; using mock HTML: %s",
            label, elapsed, exc,
        )
        event("fallback", f"调用失败（{elapsed:.0f}s），已切换 Mock 兜底")
        return {"provider": provider, "html_code": MOCK_HTML, "is_mock": True}


def _model_stream_worker(provider: str, prompt_text: str, events: queue.Queue) -> None:
    try:
        result = _stream_model(provider, prompt_text, events.put)
    except Exception:
        logger.exception("Unexpected streaming worker error for %s.", provider)
        result = {
            "provider": provider,
            "html_code": MOCK_HTML,
            "is_mock": True,
        }
    events.put({"type": "__result", "provider": provider, "result": result})


def _ndjson(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _single_prompt(prompt: str) -> str:
    return (
        "请根据以下需求，生成一个完整、可直接在浏览器中运行、可交互的单文件 HTML 网页应用。\n\n"
        f"用户需求：\n{prompt}\n\n"
        "实现要求：\n"
        "1. HTML、CSS、JavaScript 全部写在同一个文件中，不依赖构建工具。\n"
        "2. 页面所有可见文案使用用户在需求中指定的语言；未指定时使用简体中文。\n"
        "3. 功能必须完整可用，不要只输出静态稿，不要省略关键逻辑。\n"
        "4. 如果使用 React，禁止直接输出浏览器无法解析的 JSX：必须同时引入 Babel standalone，"
        "并把 JSX 脚本标记为 type=\"text/babel\"，或改用 React.createElement；"
        "确保页面无需构建步骤即可直接运行。\n"
        "5. 直接输出最终 HTML 文件内容。"
    )


def _refine_prompt(html_code: str, instruction: str, target_context: str = "") -> str:
    target_block = ""
    if target_context:
        target_block = (
            "\n本次修改由用户在预览中点击组件触发。请优先只修改以下目标组件及其必要的局部结构，"
            "除非用户明确要求全局修改，否则不要破坏其他区域：\n"
            f"{target_context}\n"
        )
    return (
        "下面是一个可以运行的完整单文件 HTML 页面：\n\n"
        f"{html_code}\n\n"
        "请按照用户的修改建议更新这个页面，保留原有可用功能，并输出修改后的完整 HTML：\n"
        f"修改建议：{instruction}\n"
        f"{target_block}\n"
        "如果修改后仍使用 React JSX，必须保留 Babel standalone 或改写为 React.createElement，"
        "不能输出浏览器无法直接执行的 JSX。\n"
        "只输出修改后的完整 HTML 文件内容。"
    )


BACKEND_SYSTEM_PROMPT = """你是资深全栈工程师。根据用户需求输出成熟、可运行的完整网站项目源码。
默认技术栈为 Django 4.2 + PostgreSQL；如果用户明确指定后端语言或框架，必须优先使用用户指定的技术栈。
无论需求看起来多简单，都必须给出完整的前后端项目目录结构，不能只输出单个 HTML 文件。
前端和后端必须按功能模块划分目录，并包含依赖清单、配置示例、数据库/路由/视图、前端模板/静态资源和部署文档 DEPLOY.md。
必须严格使用以下分段格式，不要输出解释，不要使用 Markdown 代码块围栏：
===BACKEND===
技术栈名称
===FILES===
===FILE: 相对路径===
文件完整内容
===END_FILE===
===PREVIEW_HTML===
可直接在浏览器打开的单文件前端预览，使用模拟数据，可交互
===END_PREVIEW===
要求：
1. 文件完整可运行，包含依赖清单、启动说明、数据库模型、路由、视图、模板/静态资源、配置和 DEPLOY.md。
2. 前后端目录必须分离并按业务模块组织，例如 config/、apps/<module>/、templates/、static/、tests/ 等。
3. 默认 Django 4.2 + PostgreSQL；用户指定其他后端语言/框架时必须采用用户指定技术栈。
4. 必须包含可直接本地启动的完整后端项目，不得只生成静态页面。
5. 不得使用 TODO 或省略关键实现；文件路径不能包含 ..。
6. 每个 ===FILE: 相对路径=== 后必须在文件内容结束时立即输出 ===END_FILE===；不得把后续文件内容并入 requirements.txt 或任何单个文件。requirements.txt 只能包含依赖清单。
7. PREVIEW_HTML 是用于当前平台预览的独立静态页，必须完整可交互。"""

FRONTEND_SYSTEM_PROMPT = """你是资深前端工程师。根据用户需求输出成熟、可运行的完整前端项目源码。
当前项目采用默认的前端-only 模式：不要生成 Django/Flask/Node 等后端服务，不要生成数据库、接口服务或后端依赖。
只有用户明确指定“全栈 / 前后端 / 需要后端”时，才允许生成后端代码。
如果用户指定前端框架，必须使用指定框架；否则使用原生 HTML/CSS/JavaScript，确保无需构建即可直接打开。
必须给出完整的前端目录结构，例如 index.html、src/、styles/、assets/、README.md、DEPLOY.md；
如果使用 React/Vue/Vite 等框架，还必须包含 package.json、入口文件和运行说明。
必须严格使用以下分段格式，不要输出解释，不要使用 Markdown 代码块围栏：
===BACKEND===
前端技术栈名称
===FILES===
===FILE: 相对路径===
文件完整内容
===END_FILE===
===PREVIEW_HTML===
可直接在浏览器打开的单文件前端预览，使用模拟数据，可交互
===END_PREVIEW===
要求：
1. 只包含前端代码，不得出现后端目录、数据库模型或服务器代码。
2. 文件必须完整可运行，index.html 能直接打开（或按 README 构建）。
3. 前端资源必须分层到 src/、styles/、assets/、components/ 等目录，不能只有一个 HTML。
4. 每个文件必须以 ===FILE: 相对路径=== 开始、===END_FILE=== 结束。
5. 不得使用 TODO 或省略关键实现；文件路径不能包含 ..。
6. 必须包含 README.md 和 DEPLOY.md 部署说明。
7. PREVIEW_HTML 是用于当前平台预览的独立静态页，必须完整可交互。"""

BACKEND_FRAMEWORKS = (
    ("Django", r"\bdjango\b"),
    ("Flask", r"\bflask\b"),
    ("FastAPI", r"\bfastapi\b"),
    ("Spring Boot", r"\bspring(?:\s*boot)?\b"),
    ("Express", r"\bexpress(?:\.js)?\b"),
    ("NestJS", r"\bnest(?:js)?\b"),
    ("Laravel", r"\blaravel\b"),
    ("Ruby on Rails", r"\b(?:ruby\s+on\s+rails|rails)\b"),
    ("ASP.NET", r"\basp\.?net\b"),
    ("Gin", r"\bgolang\b|\bgo\s*语言\b|\bgin\b"),
    ("Node.js", r"\bnode(?:\.js)?\b"),
    ("PHP", r"\bphp\b"),
    ("Java", r"\bjava\b"),
    ("Python", r"\bpython\b"),
)
FRONTEND_ONLY_HINTS = re.compile(
    r"(纯前端|仅前端|只要前端|只需要前端|只生成前端|只写前端|只做前端|"
    r"前端代码即可|前端项目即可|只输出前端|不生成后端|不要生成后端|"
    r"不需要生成后端|无需生成后端|不用生成后端|"
    r"不要写后端|不用写后端|不需要写后端|"
    r"不需要后端|无需后端|不用后端|不要后端|"
    r"不使用数据库|不要使用数据库|不要用数据库|不需要数据库|无需数据库|不用数据库|"
    r"no backend|frontend[\s-]*only|frontend code only)",
    re.IGNORECASE,
)
# The product default is a frontend-only project.  A full-stack bundle is only
# generated when the requirement explicitly asks for a backend/database or a
# server-side framework; broad feature words such as "login" must not silently
# opt the user into a backend.
FULLSTACK_HINTS = re.compile(
    r"(全栈|full[\s-]*stack|前后端|前端\s*[+＋和与及]\s*后端|"
    r"前端.{0,6}后端.{0,6}(?:都|一起|完整)|"
    r"(?:需要|要|带|包含|生成|开发|做|使用|用).{0,10}"
    r"(?:后端|服务端|server[\s-]*side|backend|数据库|database)|"
    r"(?:后端|服务端).{0,10}(?:代码|项目|服务|接口|api|数据库)|"
    r"完整.{0,6}(?:后端|服务端|全栈)|"
    r"\b(?:django|flask|fastapi|spring(?:\s*boot)?|express|nestjs|laravel|"
    r"ruby\s+on\s+rails|asp\.?net|gin|node(?:\.js)?)\b)",
    re.IGNORECASE,
)
FRONTEND_FRAMEWORKS = (
    ("React", r"\breact\b"),
    ("Vue", r"\bvue\b"),
    ("Svelte", r"\bsvelte\b"),
    ("Next.js", r"\bnext(?:\.js)?\b"),
    ("Nuxt", r"\bnuxt\b"),
    ("Vite", r"\bvite\b"),
    ("原生 HTML/CSS/JavaScript", ""),
)


def _wants_frontend_only(prompt: str) -> bool:
    return bool(FRONTEND_ONLY_HINTS.search(prompt))


def _wants_fullstack(prompt: str) -> bool:
    """True only when the requirement explicitly asks for a backend/full stack."""
    if _wants_frontend_only(prompt):
        return False
    return bool(FULLSTACK_HINTS.search(prompt))


def _detect_frontend_label(prompt: str) -> str:
    for label, pattern in FRONTEND_FRAMEWORKS:
        if pattern and re.search(pattern, prompt, flags=re.IGNORECASE):
            return label
    return "原生 HTML/CSS/JavaScript"


def _detect_backend_requirement(prompt: str) -> tuple[bool, str]:
    if not _wants_fullstack(prompt):
        return False, ""
    framework = ""
    for label, pattern in BACKEND_FRAMEWORKS:
        if re.search(pattern, prompt, flags=re.IGNORECASE):
            framework = label
            break
    return True, framework or "Django 4.2 + PostgreSQL"


def _backend_provider() -> str:
    for provider in ("deepseek", "qwen"):
        if _provider_is_configured(provider):
            return provider
    return "deepseek"


def _analyze_reference_image(image_data: str, job_id: str | None = None) -> str:
    """Best-effort multimodal description of the uploaded reference image."""
    if not image_data:
        return ""
    api_key = os.getenv(PROVIDERS["qwen"]["key_env"], "").strip()
    if not api_key:
        return ""
    event = _job_cancel_event(job_id) if job_id else None
    if event and event.is_set():
        return ""
    model = (
        os.getenv("QWEN_VISION_MODEL", "").strip()
        or "qwen3.8-flash"
    )
    try:
        timeout = float(os.getenv("QWEN_VISION_TIMEOUT", "30"))
    except ValueError:
        timeout = 30.0
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是资深 UI/UX 设计师，负责把参考图转成可执行的网页视觉规范。",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请用中文分析这张网页/应用参考图：页面类型、布局结构、"
                            "主色与辅助色、字体风格、组件形态、间距和交互特征。"
                            "控制在 600 字内，只输出分析，不要输出代码。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_data}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "stream": False,
    }
    request = urllib.request.Request(
        _provider_endpoint("qwen"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if job_id:
                _register_http_response(job_id, response)
            try:
                body = response.read(MAX_LLM_RESPONSE_BYTES)
            finally:
                if job_id:
                    _unregister_http_response(job_id, response)
        if event and event.is_set():
            return ""
        data = json.loads(body.decode("utf-8"))
        content = (data["choices"][0]["message"].get("content") or "").strip()
        return content[:2400]
    except Exception as exc:
        if event and event.is_set():
            return ""
        logger.warning("Reference image analysis failed: %s", exc)
        return ""


def _reference_image_context(
    image_data: str, meta: dict | None, job_id: str | None = None
) -> str:
    if not image_data:
        return ""
    parts = []
    meta = meta or {}
    width = meta.get("width")
    height = meta.get("height")
    if width and height:
        parts.append(f"参考图尺寸：{width}×{height}px。")
    palette = meta.get("palette")
    if palette:
        parts.append("参考图主色：" + "、".join(palette) + "。")
    brightness = meta.get("brightness")
    if isinstance(brightness, (int, float)):
        parts.append(f"参考图平均亮度：{brightness:.2f}（0 为黑，1 为白）。")
    description = _analyze_reference_image(image_data, job_id)
    if description:
        parts.append("参考图视觉分析：\n" + description)
    if not parts:
        parts.append("用户上传了参考图，请按其整体布局和视觉风格生成页面。")
    return "\n".join(parts)


def _backend_prompt(
    prompt: str, detected_label: str, reference_context: str = ""
) -> str:
    reference_block = (
        "\n参考图分析（请参考其布局、配色、组件、字体和视觉风格）：\n"
        f"{reference_context}\n"
        if reference_context else ""
    )
    return (
        "用户需求：\n"
        f"{prompt}\n\n"
        f"检测到的后端技术栈：{detected_label}\n"
        "如果用户已经指定后端语言或框架，优先使用用户指定的技术栈；"
        "否则使用 Django 4.2 + PostgreSQL。\n"
        "请生成完整网站项目：前端与后端目录分层，按功能模块组织；包含依赖清单、配置示例、"
        "数据库模型与迁移、路由、视图、模板/静态资源、测试或健康检查，以及详细的 DEPLOY.md 部署文档。\n"
        "不要只输出单文件 HTML。每个文件必须用 ===FILE: 相对路径=== 开始、===END_FILE=== 结束，"
        "不能把多个文件写进 requirements.txt。最后输出一个静态可交互 PREVIEW_HTML 预览。"
        f"{reference_block}"
    )


def _frontend_bundle_prompt(
    prompt: str, detected_label: str, reference_context: str = ""
) -> str:
    reference_block = (
        "\n参考图分析（请参考其布局、配色、组件、字体和视觉风格）：\n"
        f"{reference_context}\n"
        if reference_context else ""
    )
    return (
        "用户需求：\n"
        f"{prompt}\n\n"
        f"检测到的前端技术栈：{detected_label}\n"
        "当前是默认的前端-only 生成模式，未要求后端：不要生成后端服务、数据库或服务端接口。\n"
        "如果页面需要数据，请使用内置模拟数据或 localStorage，保证可直接在浏览器运行。\n"
        "请生成完整的前端项目：index.html、src/、styles/、assets/、README.md、DEPLOY.md，"
        "如果使用 React/Vue/Vite 等框架则包含 package.json 和入口文件。\n"
        "所有前端资源必须分层到目录中，不能只输出一个 HTML。\n"
        "每个文件必须用 ===FILE: 相对路径=== 开始、===END_FILE=== 结束。"
        "最后输出一个静态可交互 PREVIEW_HTML 预览。"
        f"{reference_block}"
    )


def _backend_timeout(provider: str | None = None) -> float:
    try:
        base = float(os.getenv("BACKEND_TIMEOUT", "90"))
    except ValueError:
        base = 90.0
    if provider == "qwen":
        # Qwen full-project generations are substantially slower (and the
        # Token Plan endpoint can queue requests). Keep a separate, larger
        # budget so a 90-second read timeout does not turn Qwen into a Mock
        # fallback even though DeepSeek succeeded.
        try:
            qwen_budget = float(os.getenv("QWEN_BACKEND_TIMEOUT", "180"))
        except ValueError:
            qwen_budget = 180.0
        return max(base, qwen_budget)
    return base


def _call_backend_model(
    provider: str,
    prompt: str,
    job_id: str | None = None,
    system_prompt: str = BACKEND_SYSTEM_PROMPT,
) -> str | None:
    config = PROVIDERS[provider]
    api_key = os.getenv(config["key_env"], "").strip()
    if not api_key:
        return None
    event = _job_cancel_event(job_id) if job_id else None
    if event and event.is_set():
        return None
    model = _provider_model(provider)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 24000,
        "stream": False,
    }
    request = urllib.request.Request(
        _provider_endpoint(provider),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=_backend_timeout(provider)) as response:
            if job_id:
                _register_http_response(job_id, response)
            try:
                if event and event.is_set():
                    return None
                body = response.read(MAX_LLM_RESPONSE_BYTES)
            finally:
                if job_id:
                    _unregister_http_response(job_id, response)
        if event and event.is_set():
            return None
        data = json.loads(body.decode("utf-8"))
        content = data["choices"][0]["message"]["content"] or ""
        logger.info(
            "Backend project generated by %s in %.1fs (%s chars).",
            config["label"], time.monotonic() - started, len(content),
        )
        return content
    except Exception as exc:
        if event and event.is_set():
            logger.info(
                "Backend generation for %s stopped because the task was deleted.",
                config["label"],
            )
            return None
        logger.warning(
            "Backend generation failed for %s after %.1fs: %s",
            config["label"], time.monotonic() - started, exc,
        )
        return None


def _backend_worker(provider: str, prompt: str, events: queue.Queue) -> None:
    config = PROVIDERS[provider]
    events.put(
        {
            "type": "provider",
            "provider": provider,
            "label": config["label"],
            "model": _provider_model(provider),
            "status": "streaming",
            "backend": True,
            "message": "正在生成后端项目文件与前端预览",
        }
    )
    content = _call_backend_model(provider, prompt)
    events.put(
        {
            "type": "__backend_result",
            "provider": provider,
            "content": content,
        }
    )


def _strip_file_fence(content: str) -> str:
    text = content.strip("\n")
    fenced = re.match(r"^```[\w.+-]*\s*\n(.*?)\n```\s*$", text, flags=re.DOTALL)
    return fenced.group(1) if fenced else text


def _safe_project_path(path: str) -> str | None:
    path = path.strip().replace("\\", "/").strip("/")
    if not path or len(path) > 240:
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return path


def _ensure_deployment_doc(files: list[dict], backend_label: str) -> list[dict]:
    if any(
        str(item.get("path", "")).rsplit("/", 1)[-1].lower() == "deploy.md"
        for item in files
    ):
        return files
    manage_file = next(
        (item for item in files if str(item.get("path", "")).endswith("manage.py")),
        None,
    )
    is_django = bool(manage_file) or "django" in backend_label.lower()
    if is_django:
        settings_module = "config.settings"
        if manage_file:
            match = re.search(
                r"DJANGO_SETTINGS_MODULE['\"]?\s*[,:=]\s*['\"]([^'\"]+)",
                manage_file.get("content", ""),
            )
            if match:
                settings_module = match.group(1)
        wsgi_module = settings_module.rsplit(".", 1)[0] + ".wsgi:application"
        content = textwrap.dedent(
            f"""\
            # 部署文档

            技术栈：{backend_label}

            ## 目录说明

            项目已将前端模板/静态资源与 Django 后端代码分离，并按功能模块组织。
            `preview.html` 是平台预览用的静态页面，不替代实际 Django 模板。

            ## 本地运行

            ```bash
            python3 -m venv .venv
            source .venv/bin/activate
            pip install -r requirements.txt
            # 根据项目需求配置环境变量和数据库连接
            python manage.py migrate
            python manage.py runserver 0.0.0.0:8000
            ```

            打开 http://127.0.0.1:8000/。

            ## 环境变量

            检查项目中的 `.env.example` 或配置文件，至少设置：

            ```dotenv
            DJANGO_SECRET_KEY=change-me
            DJANGO_DEBUG=True
            ALLOWED_HOSTS=127.0.0.1,localhost
            ```

            如使用 PostgreSQL，请按项目配置设置 `DATABASE_URL` 或数据库账号。

            ## 生产部署

            ```bash
            pip install gunicorn
            python manage.py migrate
            python manage.py collectstatic --noinput
            gunicorn {wsgi_module} --bind 0.0.0.0:8000 --workers 3
            ```

            建议由 Nginx 反向代理，并配置 HTTPS、静态文件和进程守护。
            """
        )
    else:
        content = textwrap.dedent(
            f"""\
            # 部署文档

            技术栈：{backend_label}

            ## 部署步骤

            1. 根据项目中的依赖清单安装运行环境。
            2. 按 `.env.example` 或配置说明设置环境变量和数据库。
            3. 执行项目提供的构建/迁移命令。
            4. 使用该技术栈推荐的生产服务器启动应用。
            5. 配置反向代理、HTTPS 和进程守护。

            `preview.html` 仅供当前平台预览，完整网站请按项目文件独立部署。
            """
        )
    files.append({"path": "DEPLOY.md", "content": content})
    return files


def _mock_django_project() -> dict:
    files = [
        {
            "path": "requirements.txt",
            "content": "Django==4.2.30\n",
        },
        {
            "path": "manage.py",
            "content": textwrap.dedent(
                """\
                #!/usr/bin/env python
                import os
                import sys


                def main():
                    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
                    from django.core.management import execute_from_command_line
                    execute_from_command_line(sys.argv)


                if __name__ == "__main__":
                    main()
                """
            ),
        },
        {"path": "config/__init__.py", "content": ""},
        {
            "path": "config/settings.py",
            "content": textwrap.dedent(
                """\
                import os
                from pathlib import Path

                BASE_DIR = Path(__file__).resolve().parent.parent
                SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "mock-fallback-dev-key")
                DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
                ALLOWED_HOSTS = [h.strip() for h in os.environ.get(
                    "ALLOWED_HOSTS", "127.0.0.1,localhost"
                ).split(",") if h.strip()]

                INSTALLED_APPS = [
                    "django.contrib.staticfiles",
                    "calculator",
                ]
                MIDDLEWARE = [
                    "django.middleware.security.SecurityMiddleware",
                    "django.middleware.common.CommonMiddleware",
                    "django.middleware.csrf.CsrfViewMiddleware",
                    "django.middleware.clickjacking.XFrameOptionsMiddleware",
                ]
                ROOT_URLCONF = "config.urls"
                TEMPLATES = [{
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [BASE_DIR / "templates"],
                    "APP_DIRS": True,
                    "OPTIONS": {"context_processors": []},
                }]
                WSGI_APPLICATION = "config.wsgi.application"
                DATABASES = {
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": BASE_DIR / "db.sqlite3",
                    }
                }
                LANGUAGE_CODE = "zh-hans"
                TIME_ZONE = "Asia/Shanghai"
                USE_I18N = True
                USE_TZ = True
                STATIC_URL = "static/"
                DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
                """
            ),
        },
        {
            "path": "config/urls.py",
            "content": textwrap.dedent(
                """\
                from django.urls import include, path

                urlpatterns = [path("", include("calculator.urls"))]
                """
            ),
        },
        {
            "path": "config/wsgi.py",
            "content": textwrap.dedent(
                """\
                import os
                from django.core.wsgi import get_wsgi_application

                os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
                application = get_wsgi_application()
                """
            ),
        },
        {
            "path": "config/asgi.py",
            "content": textwrap.dedent(
                """\
                import os
                from django.core.asgi import get_asgi_application

                os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
                application = get_asgi_application()
                """
            ),
        },
        {"path": "calculator/__init__.py", "content": ""},
        {
            "path": "calculator/apps.py",
            "content": textwrap.dedent(
                """\
                from django.apps import AppConfig


                class CalculatorConfig(AppConfig):
                    default_auto_field = "django.db.models.BigAutoField"
                    name = "calculator"
                """
            ),
        },
        {
            "path": "calculator/views.py",
            "content": textwrap.dedent(
                """\
                from django.shortcuts import render


                def index(request):
                    return render(request, "index.html")
                """
            ),
        },
        {
            "path": "calculator/urls.py",
            "content": textwrap.dedent(
                """\
                from django.urls import path
                from . import views

                urlpatterns = [path("", views.index, name="calculator")]
                """
            ),
        },
        {"path": "templates/index.html", "content": MOCK_HTML},
    ]
    files = _ensure_deployment_doc(files, "Django 4.2 (Mock 兜底)")
    return {
        "backend_label": "Django 4.2 (Mock 兜底)",
        "files": files,
        "preview_html": MOCK_HTML,
    }


FILE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*=+\s*FILE:\s*([^\n=]+?)\s*=+[ \t]*$",
    flags=re.IGNORECASE,
)
END_FILE_MARKER_RE = re.compile(
    r"(?m)^[ \t]*=+\s*END_FILE\s*=+[ \t]*(?:\n|$)",
    flags=re.IGNORECASE,
)
PREVIEW_START_RE = re.compile(r"=+\s*PREVIEW_HTML\s*=+", flags=re.IGNORECASE)
PREVIEW_END_RE = re.compile(r"=+\s*END_PREVIEW\s*=+", flags=re.IGNORECASE)


def _mock_frontend_project() -> dict:
    """A complete vanilla frontend project used when frontend-only generation fails."""
    files = [
        {
            "path": "index.html",
            "content": textwrap.dedent(
                """\
                <!doctype html>
                <html lang="zh-CN">
                <head>
                  <meta charset="utf-8">
                  <meta name="viewport" content="width=device-width, initial-scale=1">
                  <title>前端 Mock 应用</title>
                  <link rel="stylesheet" href="styles/app.css">
                </head>
                <body>
                  <main class="app">
                    <h1>前端 Mock 应用</h1>
                    <p>这是前端生成失败时使用的完整前端项目兜底。</p>
                    <button id="action" type="button">点击体验</button>
                    <p id="output">等待操作</p>
                  </main>
                  <script src="src/components/app.js" defer></script>
                  <script src="src/main.js" defer></script>
                </body>
                </html>
                """
            ),
        },
        {
            "path": "styles/app.css",
            "content": textwrap.dedent(
                """\
                :root { color-scheme: light; }
                * { box-sizing: border-box; }
                body { margin: 0; min-height: 100vh; display: grid; place-items: center;
                  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  background: linear-gradient(135deg, #eef2ff, #ecfeff); color: #101828; }
                .app { width: min(420px, calc(100vw - 32px)); padding: 28px;
                  border-radius: 20px; background: #fff; box-shadow: 0 24px 60px rgba(30,41,59,.14); }
                button { border: 0; border-radius: 10px; padding: 11px 16px;
                  color: #fff; background: linear-gradient(135deg, #4f46e5, #0891b2); font-weight: 700; }
                """
            ),
        },
        {
            "path": "src/main.js",
            "content": textwrap.dedent(
                """\
                document.addEventListener("DOMContentLoaded", () => {
                  const button = document.getElementById("action");
                  const output = document.getElementById("output");
                  let count = 0;
                  button.addEventListener("click", () => {
                    count += 1;
                    output.textContent = window.PF.renderMessage(count);
                  });
                });
                """
            ),
        },
        {
            "path": "src/components/app.js",
            "content": textwrap.dedent(
                """\
                window.PF = window.PF || {};
                window.PF.renderMessage = function (count) {
                  return `已点击 ${count} 次，前端交互正常。`;
                };
                """
            ),
        },
        {
            "path": "assets/README.md",
            "content": "# assets\n\nPlace frontend images, icons and fonts here.\n",
        },
        {
            "path": "README.md",
            "content": "# 前端项目\n\n这是一个完整的前端目录兜底项目。直接打开 `index.html`，或使用任意静态文件服务器。\n",
        },
        {
            "path": "DEPLOY.md",
            "content": textwrap.dedent(
                """\
                # 前端项目部署

                ## 目录

                - `index.html`：入口
                - `styles/`：样式
                - `src/`：脚本和组件
                - `assets/`：静态资源

                ## 本地预览

                直接打开 `index.html`，或使用：

                ```bash
                python -m http.server 8000
                ```

                ## 静态部署

                将整个目录上传到 Nginx、对象存储静态网站、Vercel、Netlify 或 GitHub Pages 即可。
                这个项目不依赖后端服务。
                """
            ),
        },
    ]
    files = _ensure_deployment_doc(files, "前端项目 · 原生 HTML/CSS/JavaScript")
    preview_html = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>前端 Mock 应用</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(135deg,#eef2ff,#ecfeff);color:#101828}
.app{width:min(420px,calc(100vw - 32px));padding:28px;border-radius:20px;background:#fff;box-shadow:0 24px 60px rgba(30,41,59,.14)}
button{border:0;border-radius:10px;padding:11px 16px;color:#fff;background:linear-gradient(135deg,#4f46e5,#0891b2);font-weight:700}
#output{margin-top:16px;color:#667085}
</style></head>
<body><main class="app"><h1>前端 Mock 应用</h1>
<p>这是前端-only 生成失败时使用的完整前端项目兜底。</p>
<button id="action" type="button">点击体验</button><p id="output">等待操作</p>
</main><script>let count=0;document.getElementById('action').addEventListener('click',function(){count+=1;document.getElementById('output').textContent='已点击 '+count+' 次，前端交互正常。';});</script></body></html>"""
    return {
        "backend_label": "前端项目 · 原生 HTML/CSS/JavaScript (Mock)",
        "files": files,
        "preview_html": preview_html,
    }


def _parse_project_bundle(raw: str, fallback_label: str) -> dict | None:
    """Parse model output into files + preview, tolerating common deviations.

    Some models use ``===FILE: path===`` as a separator but omit every
    ``===END_FILE===``, or omit the ``===FILES===`` header.  The old parser then
    swallowed the entire project into the first file (usually
    ``requirements.txt``) and saved only that file plus an auto-generated
    DEPLOY.md.  Split file sections by the next file/preview marker instead.
    """
    if not raw:
        return None

    label_match = re.search(
        r"=+\s*BACKEND\s*=+\s*(.*?)(?==+\s*FILES\s*=+|=+\s*FILE:)",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    backend_label = (
        label_match.group(1).strip() if label_match else fallback_label
    )[:120]

    end_matches = list(PREVIEW_END_RE.finditer(raw))
    if not end_matches:
        return None
    end_match = end_matches[-1]
    start_match = PREVIEW_START_RE.search(raw[: end_match.start()])
    file_region_end = start_match.start() if start_match else end_match.start()
    markers = list(FILE_MARKER_RE.finditer(raw[:file_region_end]))
    if not markers:
        return None

    files = []
    seen = set()
    total_size = 0
    for index, marker in enumerate(markers):
        path = _safe_project_path(marker.group(1))
        next_start = (
            markers[index + 1].start()
            if index + 1 < len(markers)
            else file_region_end
        )
        content = raw[marker.end():next_start]
        content = END_FILE_MARKER_RE.sub("", content)
        content = _strip_file_fence(content).strip("\n")
        if not path or path in seen:
            continue
        total_size += len(content)
        if len(files) >= 80 or total_size > 4 * 1024 * 1024:
            return None
        seen.add(path)
        files.append({"path": path, "content": content})
    if not files:
        return None

    if start_match:
        preview_segment = raw[start_match.end():end_match.start()]
    else:
        # No explicit start marker: use everything after the final file marker.
        preview_segment = raw[markers[-1].end():end_match.start()]
    preview = _extract_html(preview_segment)
    if not preview:
        return None
    preview = _ensure_runnable_html(preview)
    resolved_label = backend_label or fallback_label
    files = _ensure_deployment_doc(files, resolved_label)
    return {
        "backend_label": resolved_label,
        "files": files,
        "preview_html": preview,
    }


def _project_is_complete(project: dict | None) -> bool:
    """Reject partial bundles before they are stored as a full-stack project."""
    if not project:
        return False
    files = project.get("files") or []
    if len(files) < 6:
        return False
    paths = []
    for item in files:
        path = str(item.get("path", "")).replace("\\", "/").lower().strip("/")
        if path:
            paths.append(path)
    if len(paths) < 6:
        return False
    basenames = {path.rsplit("/", 1)[-1] for path in paths}

    has_dependency = bool(
        basenames
        & {
            "requirements.txt",
            "pyproject.toml",
            "pipfile",
            "package.json",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "go.mod",
            "composer.json",
            "gemfile",
            "cargo.toml",
        }
    ) or any(path.endswith(".csproj") for path in paths)
    has_backend = bool(
        basenames
        & {
            "manage.py",
            "wsgi.py",
            "asgi.py",
            "settings.py",
            "app.py",
            "main.py",
            "server.js",
            "pom.xml",
            "build.gradle",
            "go.mod",
            "artisan",
            "program.cs",
        }
    ) or any(
        path.startswith(("apps/", "backend/", "server/", "api/", "config/"))
        or "src/main/java/" in path
        or "src/main/kotlin/" in path
        for path in paths
    )
    has_frontend = any(
        path.endswith((".html", ".css", ".jsx", ".tsx", ".vue", ".svelte"))
        or path.startswith(("templates/", "static/", "frontend/", "client/", "public/", "web/"))
        or "/templates/" in path
        or "/static/" in path
        for path in paths
    )
    has_deploy = any(
        path.rsplit("/", 1)[-1] == "deploy.md" for path in paths
    )
    return has_dependency and has_backend and has_frontend and has_deploy


def _frontend_project_is_complete(project: dict | None) -> bool:
    """Validate a complete frontend-only bundle with a real directory tree."""
    if not project:
        return False
    files = project.get("files") or []
    if len(files) < 6:
        return False
    paths = []
    for item in files:
        path = str(item.get("path", "")).replace("\\", "/").lower().strip("/")
        if path:
            paths.append(path)
    if len(paths) < 6:
        return False
    basenames = {path.rsplit("/", 1)[-1] for path in paths}
    backend_markers = {
        "manage.py",
        "wsgi.py",
        "asgi.py",
        "settings.py",
        "requirements.txt",
        "pom.xml",
        "build.gradle",
        "go.mod",
        "artisan",
        "server.js",
        "app.py",
        "main.py",
    }
    if basenames & backend_markers:
        return False
    if any(path.startswith(("backend/", "server/")) for path in paths):
        return False
    has_html = any(path.endswith((".html", ".htm")) for path in paths)
    source_count = sum(
        1
        for path in paths
        if path.endswith((".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"))
    )
    has_subdir = any("/" in path for path in paths)
    has_deploy = any(path.rsplit("/", 1)[-1] == "deploy.md" for path in paths)
    return has_html and source_count >= 2 and has_subdir and has_deploy


PREVIEW_REPAIR_SYSTEM_PROMPT = """你是资深前端修复工程师。用户的应用预览在浏览器中白屏或渲染失败。
请根据原始需求、当前项目文件、当前预览和浏览器报错，定位根因并输出修复后的完整项目代码。
必须严格使用 ===BACKEND===、===FILES===、===FILE: 相对路径===、===END_FILE===、
===PREVIEW_HTML===、===END_PREVIEW=== 分段格式，不要输出解释或 Markdown 代码块围栏。
===PREVIEW_HTML=== 必须是无需构建、可直接在浏览器 iframe 中运行的单文件页面：
所有 CSS/JavaScript 内联；不得引用本地相对路径的 css/js/图片；不得输出浏览器无法执行的 JSX；
React/Vue 等运行时若来自 CDN，必须提供无构建可执行的方案；首屏必须出现可见内容。"""
MAX_PREVIEW_REPAIR_ISSUES = 8
MAX_PREVIEW_REPAIR_ATTEMPTS = 3


def _normalize_preview_issues(value) -> list[str]:
    """Clean browser-reported render failures for a repair prompt."""
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    issues: list[str] = []
    seen = set()
    for item in raw_items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()[:320]
        if not text or text in seen:
            continue
        seen.add(text)
        issues.append(text)
        if len(issues) >= MAX_PREVIEW_REPAIR_ISSUES:
            break
    if not issues:
        issues.append("预览页加载后没有可见内容，可能存在 JavaScript 语法错误、运行时异常或资源加载失败。")
    return issues


def _preview_is_usable(preview: str | None) -> bool:
    if not preview or len(preview.strip()) < 20:
        return False
    lower = preview.lower()
    if "<body" not in lower and "<html" not in lower:
        return False
    if lower.count("<script") > lower.count("</script>"):
        return False
    return True


def _extract_repair_html(raw: str) -> str | None:
    """Extract PREVIEW_HTML (or a raw HTML document) from a repair response."""
    if not raw:
        return None
    end_matches = list(PREVIEW_END_RE.finditer(raw))
    segment = raw
    if end_matches:
        end_match = end_matches[-1]
        start_match = PREVIEW_START_RE.search(raw[: end_match.start()])
        segment = (
            raw[start_match.end():end_match.start()]
            if start_match
            else raw[:end_match.start()]
        )
    html = _extract_html(segment)
    return _ensure_runnable_html(html) if html else None


def _preview_files_with_repair(files: list | None, preview: str) -> list[dict]:
    repaired = []
    found = False
    for item in files or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        content = str(item.get("content") or "")
        if path.replace("\\", "/").strip("/").lower() in {"preview.html", "preview.htm"}:
            content = preview
            found = True
        repaired.append({"path": path, "content": content})
    if not found:
        repaired.append({"path": "preview.html", "content": preview})
    return repaired


def _preview_repair_prompt(
    history: GenerateHistory,
    version: GenerateVersion,
    issues: list[str],
) -> str:
    files = version.files or []
    priority = (
        "preview.html",
        "index.html",
        "package.json",
        "vite.config",
        "src/main",
        "src/app",
        "src/index",
        "styles/",
        "style.css",
        "app.js",
        "main.js",
        "main.css",
    )

    def rank(item: dict) -> tuple[int, str]:
        path = str(item.get("path") or "").lower()
        for index, marker in enumerate(priority):
            if path.endswith(marker) or marker in path:
                return index, path
        if path.endswith((".html", ".css", ".js", ".jsx", ".tsx", ".vue", ".svelte")):
            return len(priority), path
        return len(priority) + 1, path

    file_blocks = []
    total_chars = 0
    for item in sorted(
        (entry for entry in files if isinstance(entry, dict)),
        key=rank,
    ):
        path = str(item.get("path") or "unknown")
        content = str(item.get("content") or "")
        remaining = 70000 - total_chars
        if remaining <= 0:
            break
        content = content[: min(remaining, 30000)]
        total_chars += len(content)
        file_blocks.append(f"===FILE: {path}===\n{content}\n===END_FILE===")
    if not file_blocks:
        file_blocks.append("===FILE: preview.html===\n" + (version.html_code or ""))

    issue_text = "\n".join(f"- {issue}" for issue in issues)
    preview = (version.html_code or "")[:50000]
    return (
        "用户原始需求：\n"
        f"{history.prompt}\n\n"
        "浏览器检测到预览页渲染失败，已确认的问题：\n"
        f"{issue_text}\n\n"
        "当前项目文件：\n"
        + "\n".join(file_blocks)
        + "\n\n当前 PREVIEW_HTML：\n"
        "===PREVIEW_HTML===\n"
        f"{preview}\n"
        "===END_PREVIEW===\n\n"
        "请修复导致白屏/渲染失败的根因，并输出完整可运行的项目文件包和修复后的 "
        "PREVIEW_HTML。必须保持用户原有的功能和视觉需求，不能只输出错误说明。"
        "如果问题来自本地相对路径资源、JSX、浏览器不支持的语法、提前访问 DOM、"
        "React/Vue 挂载失败或语法错误，请在修复后的代码中直接解决。"
    )


def _apply_preview_repair(
    history: GenerateHistory,
    version: GenerateVersion,
    preview: str,
    files: list | None = None,
    backend_label: str | None = None,
) -> tuple[GenerateHistory, GenerateVersion]:
    """Replace the failed preview/code in place; no extra version is created."""
    if not _preview_is_usable(preview):
        raise RuntimeError("修复结果不是可运行的 HTML 页面。")
    with transaction.atomic():
        locked_history = GenerateHistory.objects.select_for_update().get(pk=history.pk)
        locked_version = GenerateVersion.objects.select_for_update().get(pk=version.pk)
        repaired_files = (
            files
            if files is not None
            else _preview_files_with_repair(locked_version.files or [], preview)
        )
        note = locked_version.note or "初始生成"
        if "预览修复" not in note:
            note = f"{note} · 预览修复"
        locked_version.html_code = preview
        locked_version.files = repaired_files
        locked_version.note = note[:500]
        locked_version.save(update_fields=["html_code", "files", "note"])
        fields = ["html_code"]
        locked_history.html_code = preview
        if backend_label:
            locked_history.backend_label = backend_label
            fields.append("backend_label")
        locked_history.save(update_fields=fields)
    history.refresh_from_db()
    version.refresh_from_db()
    return history, version


REFERENCE_IMAGE_RE = re.compile(
    r"^data:image/(png|jpe?g|webp|gif);base64,([A-Za-z0-9+/=\s]+)$",
    flags=re.IGNORECASE,
)


def _normalize_reference_image(value) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("参考图格式不正确。")
    match = REFERENCE_IMAGE_RE.match(value.strip())
    if not match:
        raise ValueError("参考图必须是 PNG/JPG/WebP/GIF 的 base64 数据。")
    mime = match.group(1).lower()
    if mime == "jpg":
        mime = "jpeg"
    payload = re.sub(r"\s+", "", match.group(2))
    if len(payload) > REFERENCE_IMAGE_MAX_CHARS:
        raise ValueError("参考图过大，请压缩后重试（上限约 2.5MB）。")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("参考图 base64 数据无效。") from exc
    if len(decoded) > REFERENCE_IMAGE_MAX_BYTES:
        raise ValueError("参考图过大，请压缩后重试（上限约 2.5MB）。")
    return f"data:image/{mime};base64,{payload}"


def _normalize_image_meta(value) -> dict:
    if not isinstance(value, dict):
        return {}
    meta = {}
    width = value.get("width")
    height = value.get("height")
    if isinstance(width, (int, float)) and 0 < width <= 20000:
        meta["width"] = int(width)
    if isinstance(height, (int, float)) and 0 < height <= 20000:
        meta["height"] = int(height)
    brightness = value.get("brightness")
    if isinstance(brightness, (int, float)) and 0 <= brightness <= 1:
        meta["brightness"] = round(float(brightness), 3)
    palette = value.get("palette")
    if isinstance(palette, list):
        cleaned = [
            str(color)[:20]
            for color in palette[:6]
            if isinstance(color, str) and re.match(r"^#[0-9a-fA-F]{6}$", color)
        ]
        if cleaned:
            meta["palette"] = cleaned
    name = value.get("name")
    if isinstance(name, str) and name.strip():
        meta["name"] = re.sub(r"\s+", " ", name).strip()[:120]
    return meta


def _read_json(request) -> dict:
    try:
        data = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("请求体必须是合法的 JSON。") from exc
    if not isinstance(data, dict):
        raise ValueError("请求体格式不正确。")
    return data


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _publication_payload(publication: PublishedPage, request=None) -> dict:
    path = reverse("generator:published-page", kwargs={"slug": publication.slug})
    return {
        "slug": publication.slug,
        "path": path,
        "url": request.build_absolute_uri(path) if request else path,
        "created_at": publication.created_at.isoformat(),
    }


def _version_payload(
    version: GenerateVersion,
    include_html: bool = False,
    request=None,
) -> dict:
    publication = getattr(version, "publication", None)
    payload = {
        "id": version.id,
        "version_no": version.version_no,
        "note": version.note,
        "created_at": version.created_at.isoformat(),
        "is_mock": version.html_code.strip() == MOCK_HTML.strip(),
        "base_version_id": version.base_version_id,
        "base_version_no": (
            version.base_version.version_no if version.base_version_id else None
        ),
        "published": _publication_payload(publication, request) if publication else None,
    }
    if include_html:
        payload["html_code"] = _repair_babel_preview(version.html_code)
        payload["files"] = version.files or []
    return payload


def _history_payload(
    history: GenerateHistory,
    include_html: bool = False,
    request=None,
) -> dict:
    versions = list(history.versions.all())
    version_payloads = [
        _version_payload(version, include_html=include_html, request=request)
        for version in versions
    ]
    provider = history.model_provider if history.model_provider in PROVIDERS else "deepseek"
    return {
        "id": history.id,
        "prompt": history.prompt,
        "created_at": history.created_at.isoformat(),
        "version_count": len(versions),
        "published_count": sum(1 for version in version_payloads if version["published"]),
        "model_provider": provider,
        "model_label": PROVIDERS[provider]["label"],
        "assistant_name": _assistant_name(provider),
        "project_type": history.project_type,
        "backend_label": history.backend_label,
        "versions": version_payloads,
    }


def _assistant_name(provider: str) -> str:
    return "QW 机器人" if provider == "qwen" else "DP 机器人"


def _create_history(
    prompt: str,
    html_code: str,
    note: str,
    provider: str = "deepseek",
    files: list | None = None,
    project_type: str = "frontend",
    backend_label: str = "",
) -> GenerateHistory:
    if provider not in PROVIDERS:
        provider = "deepseek"
    with transaction.atomic():
        history = GenerateHistory.objects.create(
            prompt=prompt,
            html_code=html_code,
            model_provider=provider,
            project_type=project_type,
            backend_label=backend_label,
        )
        GenerateVersion.objects.create(
            history=history,
            version_no=1,
            html_code=html_code,
            note=note,
            files=files or [],
        )
    return history


@ensure_csrf_cookie
def index(request):
    return render(
        request,
        "index.html",
        {
            "page": "home",
            "deepseek_configured": _provider_is_configured("deepseek"),
            "qwen_configured": _provider_is_configured("qwen"),
        },
    )


@ensure_csrf_cookie
def workspace(request):
    return render(
        request,
        "index.html",
        {
            "page": "workspace",
            "deepseek_configured": _provider_is_configured("deepseek"),
            "qwen_configured": _provider_is_configured("qwen"),
        },
    )


def _race_model_payload(
    provider: str, html_code: str, is_mock: bool, prompt: str = ""
) -> dict:
    try:
        score = score_html(html_code, is_mock, prompt)
    except Exception:
        logger.exception("Scoring failed for %s; assigning zero score.", provider)
        score = score_html("", True, prompt)
        score["grade"] = "评分异常"
        score["summary"] = "评分引擎无法解析该页面，按 0 分处理"
    return {
        "provider": provider,
        "label": PROVIDERS[provider]["label"],
        "model": _provider_model(provider),
        "is_mock": is_mock,
        "score": score,
    }


def _pick_race_winner(models: list[dict]) -> tuple[dict, bool]:
    # Mock fallback is never allowed to beat a real model response.
    available = [model for model in models if not model["is_mock"]]
    pool = available or models
    winner = max(
        pool,
        key=lambda model: (
            model["score"]["total"],
            model["score"].get("tie_breaker", 0),
        ),
    )
    tied = (
        sum(
            1
            for model in pool
            if model["score"]["total"] == winner["score"]["total"]
            and model["score"].get("tie_breaker", 0)
            == winner["score"].get("tie_breaker", 0)
        )
        > 1
    )
    return winner, tied


def _race_note(models: list[dict], winner: dict, tied: bool) -> str:
    if all(model["is_mock"] for model in models):
        return "双模型赛马 · 双方均调用失败，Mock 兜底"
    rivals = [
        f"{model['label']} {model['score']['total']}分"
        for model in models
        if model["provider"] != winner["provider"] and not model["is_mock"]
    ]
    label = f"双模型赛马 · {winner['label']}胜出（{winner['score']['total']}分"
    if rivals:
        label += f"；对比 {' / '.join(rivals)}"
    label += "）"
    if tied:
        label += " · 同分按内容完整度决胜"
    failed = [
        model["label"]
        for model in models
        if model["is_mock"] and model["provider"] != winner["provider"]
    ]
    if failed:
        label += f" · {'、'.join(failed)}调用失败"
    elif any(model["is_mock"] for model in models):
        label += " · 含 Mock 兜底"
    return label


@require_GET
def history_list(request):
    histories = (
        GenerateHistory.objects.prefetch_related("versions__publication")
        .order_by("-created_at")
    )
    return JsonResponse(
        {
            "histories": [
                _history_payload(history, request=request) for history in histories
            ]
        }
    )


@require_GET
def race_stats(request):
    """Aggregate historical race winners for the single-model recommender."""
    jobs = GenerationJob.objects.filter(
        requested_mode="race",
        status=GenerationJob.STATUS_COMPLETED,
    ).order_by("-created_at")
    providers = ["deepseek", "qwen"]
    stats = {
        provider: {
            "provider": provider,
            "label": PROVIDERS[provider]["label"],
            "model": _provider_model(provider),
            "configured": _provider_is_configured(provider),
            "wins": 0,
            "races": 0,
            "score_sum": 0.0,
            "score_count": 0,
        }
        for provider in providers
    }
    races = []
    total_races = 0

    for job in jobs:
        state = job.state if isinstance(job.state, dict) else {}
        race = state.get("race") or {}
        models = race.get("models") or []
        winner = race.get("winner") or {}
        winner_provider = winner.get("provider")
        if winner_provider not in stats or winner.get("is_mock"):
            continue
        real_scores = {}
        failed_providers = set()
        for model in models:
            provider = model.get("provider")
            if provider not in stats:
                continue
            if model.get("is_mock"):
                failed_providers.add(provider)
                continue
            score = (model.get("score") or {}).get("total")
            if isinstance(score, (int, float)):
                real_scores[provider] = float(score)
        winner_score = real_scores.get(winner_provider)
        if winner_score is None:
            continue
        loser = "qwen" if winner_provider == "deepseek" else "deepseek"
        loser_score = real_scores.get(loser)
        loser_label = stats[loser]["label"]
        winner_label = stats[winner_provider]["label"]

        total_races += 1
        stats[winner_provider]["wins"] += 1
        for provider in providers:
            stats[provider]["races"] += 1
        stats[winner_provider]["score_sum"] += winner_score
        stats[winner_provider]["score_count"] += 1
        if loser_score is not None:
            stats[loser]["score_sum"] += loser_score
            stats[loser]["score_count"] += 1

        if loser_score is not None:
            note = (
                f"双模型赛马 · {winner_label}胜出（{winner_score:.1f}分；"
                f"对比 {loser_label} {loser_score:.1f}分）"
            )
        elif loser in failed_providers:
            note = (
                f"双模型赛马 · {winner_label}胜出（{winner_score:.1f}分） · "
                f"{loser_label}调用失败"
            )
        else:
            note = f"双模型赛马 · {winner_label}胜出（{winner_score:.1f}分）"

        races.append(
            {
                "history_id": job.history_id,
                "prompt": job.prompt,
                "created_at": job.created_at.isoformat(),
                "winner": winner_provider,
                "winner_label": winner_label,
                "winner_score": winner_score,
                "loser": loser,
                "loser_label": loser_label,
                "loser_score": loser_score,
                "note": note,
            }
        )

    model_list = []
    for provider in providers:
        item = stats[provider]
        win_rate = (item["wins"] / total_races * 100) if total_races else 0
        avg_score = (
            item["score_sum"] / item["score_count"]
            if item["score_count"]
            else None
        )
        model_list.append(
            {
                "provider": provider,
                "label": item["label"],
                "model": item["model"],
                "configured": item["configured"],
                "wins": item["wins"],
                "races": item["races"],
                "win_rate": round(win_rate, 1),
                "avg_score": round(avg_score, 1) if avg_score is not None else None,
            }
        )

    recommended = None
    if total_races and len(model_list) == 2:
        ranked = sorted(
            model_list,
            key=lambda item: (item["wins"], item["win_rate"]),
            reverse=True,
        )
        first, second = ranked
        if first["wins"] > second["wins"]:
            recommended = first["provider"]

    return JsonResponse(
        {
            "total_races": total_races,
            "models": model_list,
            "recommended": recommended,
            "races": races,
        }
    )


@require_GET
def history_detail(request, history_id: int):
    history = get_object_or_404(
        GenerateHistory.objects.prefetch_related("versions__publication"),
        pk=history_id,
    )
    return JsonResponse(
        {"history": _history_payload(history, include_html=True, request=request)}
    )


def _normalize_client_id(value) -> str:
    raw = value if isinstance(value, str) else ""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:64]
    return cleaned or "anonymous"


def _job_payload(job: GenerationJob, request=None, include_history: bool = False) -> dict:
    history_payload = None
    if include_history and job.history_id and job.status == GenerationJob.STATUS_COMPLETED:
        history_payload = _history_payload(
            job.history, include_html=True, request=request
        )
    state = job.state if isinstance(job.state, dict) else {}
    return {
        "id": str(job.id),
        "kind": state.get("kind") or "generate",
        "prompt": job.prompt,
        "requested_mode": job.requested_mode,
        "mode": job.mode,
        "status": job.status,
        "progress": job.progress,
        "stage_title": job.stage_title,
        "stage_message": job.stage_message,
        "state": state,
        "project_type": job.project_type,
        "backend_label": job.backend_label,
        "has_reference_image": bool(getattr(job, "reference_image", "")),
        "history_id": job.history_id,
        "target_history_id": state.get("target_history_id"),
        "target_version_id": state.get("target_version_id"),
        "target_version_no": state.get("target_version_no"),
        "target_project_prompt": state.get("target_project_prompt", ""),
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "history": history_payload,
    }


class JobCancelled(Exception):
    """Raised inside a background job after the user deletes it."""


_JOB_CANCEL_LOCK = threading.Lock()
_JOB_CANCEL_EVENTS: dict[str, threading.Event] = {}
_JOB_HTTP_RESPONSES: dict[str, set] = {}


def _register_job_cancel(job_id: str) -> threading.Event:
    event = threading.Event()
    with _JOB_CANCEL_LOCK:
        _JOB_CANCEL_EVENTS[str(job_id)] = event
    return event


def _job_cancel_event(job_id) -> threading.Event | None:
    with _JOB_CANCEL_LOCK:
        return _JOB_CANCEL_EVENTS.get(str(job_id))


def _ensure_job_active(job_id) -> None:
    event = _job_cancel_event(job_id)
    if event and event.is_set():
        raise JobCancelled()


def _unregister_job_cancel(job_id: str) -> None:
    with _JOB_CANCEL_LOCK:
        _JOB_CANCEL_EVENTS.pop(str(job_id), None)
        responses = list(_JOB_HTTP_RESPONSES.pop(str(job_id), set()))
    for response in responses:
        try:
            response.close()
        except Exception:
            pass


def _close_http_responses(responses: list) -> None:
    for response in responses:
        try:
            response.close()
        except Exception:
            pass


def _cancel_job_requests(job_id: str) -> None:
    with _JOB_CANCEL_LOCK:
        event = _JOB_CANCEL_EVENTS.get(str(job_id))
        responses = list(_JOB_HTTP_RESPONSES.get(str(job_id), set()))
    if event:
        event.set()
    if responses:
        # Closing a socket while another thread is blocked in read() can take
        # seconds. Never make the delete HTTP request wait for it: the cancel
        # event already guarantees the worker cannot persist the task again.
        threading.Thread(
            target=_close_http_responses,
            args=(responses,),
            daemon=True,
            name=f"job-cancel-{job_id}",
        ).start()


def _register_http_response(job_id: str, response) -> None:
    with _JOB_CANCEL_LOCK:
        _JOB_HTTP_RESPONSES.setdefault(str(job_id), set()).add(response)


def _unregister_http_response(job_id: str, response) -> None:
    with _JOB_CANCEL_LOCK:
        responses = _JOB_HTTP_RESPONSES.get(str(job_id))
        if responses:
            responses.discard(response)


def _save_job(job: GenerationJob) -> None:
    last_error = None
    for attempt in range(4):
        event = _job_cancel_event(job.pk)
        if event and event.is_set():
            raise JobCancelled()
        try:
            updated = GenerationJob.objects.filter(pk=job.pk).update(
                client_id=job.client_id,
                prompt=job.prompt,
                requested_mode=job.requested_mode,
                mode=job.mode,
                status=job.status,
                progress=job.progress,
                stage_title=job.stage_title,
                stage_message=job.stage_message,
                state=job.state,
                project_type=job.project_type,
                backend_label=job.backend_label,
                history_id=job.history_id,
                error=job.error,
                updated_at=timezone.now(),
            )
            if not updated:
                raise JobCancelled()
            job.updated_at = timezone.now()
            return
        except OperationalError as exc:
            last_error = exc
            close_old_connections()
            time.sleep(0.25 * (attempt + 1))
    if last_error:
        raise last_error


def _job_stage(state: dict, key: str, title: str, message: str) -> None:
    stages = state.setdefault("stages", [])
    for stage in stages:
        if stage.get("status") == "active":
            stage["status"] = "done"
    current = next((stage for stage in stages if stage.get("key") == key), None)
    if current:
        current.update(
            {"title": title, "message": message, "status": "active"}
        )
    else:
        stages.append(
            {"key": key, "title": title, "message": message, "status": "active"}
        )


def _finish_job_stages(state: dict) -> None:
    for stage in state.get("stages", []):
        if stage.get("status") == "active":
            stage["status"] = "done"


def _fail_job_stages(state: dict) -> None:
    for stage in state.get("stages", []):
        if stage.get("status") == "active":
            stage["status"] = "error"


def _provider_working_message(elapsed: float, backend: bool = False) -> str:
    if backend:
        if elapsed < 5:
            return "正在分析数据模型与接口边界"
        if elapsed < 15:
            return "正在生成后端项目文件与配置"
        if elapsed < 35:
            return "正在生成视图、路由和数据库模型"
        return "正在整理项目结构与前端预览"
    if elapsed < 3:
        return "正在理解需求并规划页面结构"
    if elapsed < 8:
        return "正在编写 HTML 与视觉样式"
    if elapsed < 18:
        return "正在实现交互逻辑与状态更新"
    if elapsed < 30:
        return "正在完善响应式与细节"
    return "正在检查完整性并准备收尾"


def _job_progress_for_elapsed(elapsed: float, max_seconds: float) -> int:
    return min(84, 16 + int(min(elapsed / max(max_seconds, 1), 1) * 68))


def _run_frontend_job(job: GenerationJob) -> None:
    mode = job.mode if job.mode in {"single", "race"} else "single"
    providers = list(RACE_PROVIDERS) if mode == "race" else ["deepseek"]
    prompt_text = _single_prompt(job.prompt)
    state = job.state or {}
    state.setdefault("order", providers)
    state.setdefault("providers", {})
    state.setdefault("stages", [])
    state["used_mock"] = False
    state["race"] = None
    for provider in providers:
        state["providers"][provider] = {
            "provider": provider,
            "label": PROVIDERS[provider]["label"],
            "model": _provider_model(provider),
            "status": "streaming",
            "message": "正在连接模型并准备生成",
            "chars": 0,
        }
    _job_stage(state, "prepare", "解析需求", "需求已进入后台生成队列")
    _job_stage(
        state,
        "generate",
        "模型生成",
        "DeepSeek 与千问正在并行生成" if mode == "race" else "DeepSeek 正在生成页面",
    )
    job.state = state
    job.status = GenerationJob.STATUS_RUNNING
    job.progress = 16
    job.stage_title = "模型生成"
    job.stage_message = (
        "DeepSeek 与千问正在并行生成" if mode == "race" else "DeepSeek 正在生成页面"
    )
    _save_job(job)

    events: queue.Queue = queue.Queue()

    def worker(provider: str) -> None:
        try:
            result = _call_model(provider, prompt_text)
        except Exception:
            logger.exception("Background provider call failed for %s.", provider)
            result = _mock_result()
        events.put((provider, result))

    threads = []
    for provider in providers:
        thread = threading.Thread(
            target=worker, args=(provider,), daemon=True, name=f"job-{job.id}-{provider}"
        )
        thread.start()
        threads.append(thread)

    results: dict[str, tuple[str, bool]] = {}
    pending = set(providers)
    started_at = time.monotonic()
    last_save = time.monotonic()
    max_seconds = max(_provider_timeout(provider) for provider in providers)
    while pending:
        try:
            provider, (html_code, is_mock) = events.get(timeout=1)
        except queue.Empty:
            elapsed = time.monotonic() - started_at
            for provider_key in pending:
                provider_state = state["providers"][provider_key]
                provider_state["message"] = _provider_working_message(elapsed)
            state_progress = _job_progress_for_elapsed(elapsed, max_seconds)
            job.progress = max(job.progress, state_progress)
            job.stage_message = " · ".join(
                f"{state['providers'][key]['label']}：{state['providers'][key]['message']}"
                for key in providers
            )
            if time.monotonic() - last_save >= 1.2:
                job.state = state
                _save_job(job)
                last_save = time.monotonic()
            continue

        results[provider] = (html_code, is_mock)
        pending.discard(provider)
        provider_state = state["providers"][provider]
        provider_state["chars"] = len(html_code)
        provider_state["status"] = "fallback" if is_mock else "done"
        provider_state["message"] = (
            "调用失败，已使用 Mock 兜底"
            if is_mock
            else "页面生成完成"
        )
        job.progress = max(job.progress, 55 if pending else 82)
        job.state = state
        _save_job(job)

    if mode == "race":
        _job_stage(state, "score", "网页质量评审", "正在按十维标准比较两个模型的结果")
        job.progress = 88
        job.stage_title = "网页质量评审"
        job.stage_message = "正在按十维标准比较两个模型的结果"
        models = [
            _race_model_payload(
                provider,
                results[provider][0],
                results[provider][1],
                job.prompt,
            )
            for provider in providers
        ]
        winner, tied = _pick_race_winner(models)
        winner_html = results[winner["provider"]][0]
        note = _race_note(models, winner, tied)
        provider = winner["provider"]
        used_mock = any(model["is_mock"] for model in models)
        state["race"] = {"models": models, "winner": winner, "tied": tied}
    else:
        winner_html, is_mock = results["deepseek"]
        note = "初始生成"
        provider = "deepseek"
        used_mock = is_mock
        state["race"] = None

    _job_stage(state, "save", "保存项目", "正在写入数据库并创建首个版本")
    job.progress = 96
    job.stage_title = "保存项目"
    job.stage_message = "正在写入数据库并创建首个版本"
    state["used_mock"] = used_mock
    job.state = state
    _save_job(job)

    history = _create_history(
        job.prompt, winner_html, note, provider=provider
    )
    _finish_job_stages(state)
    job.history = history
    job.status = GenerationJob.STATUS_COMPLETED
    job.progress = 100
    job.stage_title = "生成完成"
    job.stage_message = "项目已保存，可在工作台预览和修改"
    job.project_type = "frontend"
    job.state = state
    _save_job(job)


def _run_backend_job(job: GenerationJob, frontend_only: bool = False) -> None:
    requested_mode = job.requested_mode if job.requested_mode in {"single", "race"} else "single"
    state = job.state if isinstance(job.state, dict) else {}
    if requested_mode == "race":
        providers = list(RACE_PROVIDERS)
        state["single_provider"] = None
    else:
        selected_provider = state.get("single_provider")
        if selected_provider not in PROVIDERS:
            selected_provider = _backend_provider()
        providers = [selected_provider]
        state["single_provider"] = selected_provider
    if not providers:
        providers = ["deepseek"]
    detected_label = job.backend_label or (
        "原生 HTML/CSS/JavaScript" if frontend_only else "Django 4.2 + PostgreSQL"
    )
    project_kind = "前端项目" if frontend_only else "全栈项目"
    state["order"] = providers
    state["providers"] = {
        provider: {
            "provider": provider,
            "label": PROVIDERS[provider]["label"],
            "model": _provider_model(provider),
            "status": "streaming",
            "message": "正在生成完整项目文件与前端预览",
            "chars": 0,
            "backend": True,
        }
        for provider in providers
    }
    state["stages"] = []
    state["used_mock"] = False
    state["race"] = None
    reference_image = getattr(job, "reference_image", "") or ""
    image_meta = state.get("reference_image_meta") or {}
    state["has_reference_image"] = bool(reference_image)
    _job_stage(
        state,
        "prepare",
        "需求分析",
        f"正在准备{'前端' if frontend_only else '前后端'}项目生成",
    )
    if reference_image:
        _job_stage(
            state,
            "image",
            "参考图分析",
            "正在解析参考图配色、布局与组件",
        )
    else:
        _job_stage(
            state,
            "backend",
            f"{project_kind}生成",
            f"正在生成 {detected_label} 项目目录和前端预览",
        )
    job.state = state
    job.mode = requested_mode
    job.project_type = "frontend" if frontend_only else "backend"
    job.status = GenerationJob.STATUS_RUNNING
    job.progress = 8 if reference_image else 12
    job.stage_title = "参考图分析" if reference_image else f"{project_kind}生成"
    job.stage_message = (
        "正在解析参考图的视觉风格"
        if reference_image
        else f"正在生成 {detected_label} 项目目录和前端预览"
    )
    _save_job(job)

    reference_context = ""
    if reference_image:
        _ensure_job_active(job.pk)
        reference_context = _reference_image_context(
            reference_image, image_meta, job_id=str(job.id)
        )
        _ensure_job_active(job.pk)
        _job_stage(
            state,
            "backend",
            f"{project_kind}生成",
            f"正在生成 {detected_label} 项目目录和前端预览",
        )
        job.state = state
        job.progress = max(job.progress, 12)
        job.stage_title = f"{project_kind}生成"
        job.stage_message = f"正在生成 {detected_label} 项目目录和前端预览"
        _save_job(job)

    events: queue.Queue = queue.Queue()
    prompt_text = (
        _frontend_bundle_prompt(job.prompt, detected_label, reference_context)
        if frontend_only
        else _backend_prompt(job.prompt, detected_label, reference_context)
    )
    system_prompt = FRONTEND_SYSTEM_PROMPT if frontend_only else BACKEND_SYSTEM_PROMPT

    def worker(provider: str) -> None:
        try:
            content = _call_backend_model(
                provider,
                prompt_text,
                job_id=str(job.id),
                system_prompt=system_prompt,
            )
        except Exception:
            logger.exception("Background project-bundle call failed for %s.", provider)
            content = None
        events.put((provider, content))

    for provider in providers:
        threading.Thread(
            target=worker,
            args=(provider,),
            daemon=True,
            name=f"job-{job.id}-backend-{provider}",
        ).start()

    started_at = time.monotonic()
    last_save = time.monotonic()
    max_backend_seconds = max(_backend_timeout(provider) for provider in providers)
    contents: dict[str, str | None] = {}
    pending = set(providers)
    while pending:
        _ensure_job_active(job.pk)
        try:
            provider, content = events.get(timeout=1)
        except queue.Empty:
            elapsed = time.monotonic() - started_at
            for provider_key in pending:
                provider_state = state["providers"][provider_key]
                provider_state["message"] = _provider_working_message(
                    elapsed, backend=not frontend_only
                )
            job.progress = max(
                job.progress,
                _job_progress_for_elapsed(elapsed, max_backend_seconds),
            )
            job.stage_message = " · ".join(
                f"{state['providers'][key]['label']}：{state['providers'][key]['message']}"
                for key in providers
            )
            if time.monotonic() - last_save >= 1.2:
                job.state = state
                _save_job(job)
                last_save = time.monotonic()
            continue

        contents[provider] = content
        pending.discard(provider)
        provider_state = state["providers"][provider]
        provider_state["message"] = "模型已返回，正在解析项目文件"
        job.progress = max(job.progress, 60 if pending else 82)
        job.state = state
        _save_job(job)

    _job_stage(state, "files", "整理项目文件", "正在解析完整目录结构、部署文档与静态预览")
    job.progress = max(job.progress, 86)
    job.stage_title = "整理项目文件"
    job.stage_message = "正在解析完整目录结构、部署文档与静态预览"
    job.state = state
    _save_job(job)

    parsed_projects: dict[str, dict] = {}
    for provider in providers:
        raw_content = contents.get(provider) or ""
        parsed = _parse_project_bundle(raw_content, detected_label)
        provider_state = state["providers"][provider]
        is_complete = (
            _frontend_project_is_complete(parsed)
            if frontend_only
            else _project_is_complete(parsed)
        )
        if parsed and is_complete:
            parsed_projects[provider] = parsed
            provider_state["status"] = "done"
            provider_state["chars"] = sum(
                len(item["content"]) for item in parsed["files"]
            )
            provider_state["message"] = f"已生成 {len(parsed['files'])} 个项目文件"
        else:
            provider_state["status"] = "fallback"
            provider_state["chars"] = len(raw_content)
            if parsed:
                logger.warning(
                    "Project bundle from %s is incomplete (%s files); not scored.",
                    provider,
                    len(parsed.get("files") or []),
                )
                provider_state["message"] = (
                    f"项目结构不完整（仅 {len(parsed['files'])} 个文件），未参与评分"
                )
            elif raw_content:
                logger.warning(
                    "Project bundle from %s could not be parsed; not scored.",
                    provider,
                )
                provider_state["message"] = "返回格式无法解析，未参与评分"
            else:
                provider_state["message"] = "调用超时或接口失败，未参与评分"

    if requested_mode == "race":
        _job_stage(
            state,
            "score",
            "方案评分",
            "正在比较候选项目的页面质量与结构完整度",
        )
        job.progress = 92
        job.stage_title = "方案评分"
        job.stage_message = "正在比较候选项目的页面质量与结构完整度"
        models = []
        for provider in providers:
            if provider in parsed_projects:
                models.append(
                    _race_model_payload(
                        provider,
                        parsed_projects[provider]["preview_html"],
                        False,
                        job.prompt,
                    )
                )
            else:
                models.append(
                    _race_model_payload(provider, MOCK_HTML, True, job.prompt)
                )
        # Prefer a complete project even if the other candidate's standalone
        # preview scored higher. A pretty preview is useless if its bundle was
        # parsed into only a couple of files.
        complete_models = [
            model for model in models if model["provider"] in parsed_projects
        ]
        winner_model, tied = _pick_race_winner(
            complete_models or models
        )
        winner_provider = (
            winner_model["provider"] if winner_model else providers[0]
        )
        fallback_project = (
            _mock_frontend_project() if frontend_only else _mock_django_project()
        )
        winner_project = parsed_projects.get(winner_provider) or fallback_project
        note = _race_note(models, winner_model, tied)
        if frontend_only:
            note = "前端项目 · " + note
        used_mock = any(model["is_mock"] for model in models)
        state["race"] = {
            "models": models,
            "winner": winner_model,
            "tied": tied,
        }
    else:
        winner_provider = providers[0]
        fallback_project = (
            _mock_frontend_project() if frontend_only else _mock_django_project()
        )
        winner_project = parsed_projects.get(winner_provider) or fallback_project
        used_mock = winner_provider not in parsed_projects
        if frontend_only:
            note = (
                f"前端项目 · {winner_project['backend_label']}"
                if not used_mock
                else "前端项目 · Mock 前端兜底"
            )
        else:
            note = (
                f"全栈项目 · {winner_project['backend_label']}"
                if not used_mock
                else "全栈项目 · Mock Django 兜底"
            )
        state["race"] = None

    if reference_image:
        note += " · 含参考图"

    _job_stage(
        state,
        "save",
        "保存项目",
        f"正在写入完整{project_kind}文件并创建首个版本",
    )
    job.progress = 96
    job.stage_title = "保存项目"
    job.stage_message = f"正在写入完整{project_kind}文件并创建首个版本"
    job.state = state
    _save_job(job)
    _ensure_job_active(job.pk)

    history = _create_history(
        job.prompt,
        winner_project["preview_html"],
        note,
        provider=winner_provider,
        files=winner_project["files"],
        project_type="frontend" if frontend_only else "backend",
        backend_label=winner_project["backend_label"],
    )
    try:
        _ensure_job_active(job.pk)
        _finish_job_stages(state)
        state["used_mock"] = used_mock
        job.history = history
        job.status = GenerationJob.STATUS_COMPLETED
        job.progress = 100
        job.stage_title = "生成完成"
        job.stage_message = f"完整{project_kind}已保存，可查看目录源码或下载 ZIP"
        job.project_type = "frontend" if frontend_only else "backend"
        job.backend_label = winner_project["backend_label"]
        job.state = state
        _save_job(job)
        _ensure_job_active(job.pk)
    except JobCancelled:
        history.delete()
        raise


def _create_refine_version(
    history: GenerateHistory,
    html_code: str,
    note: str,
    files: list | None = None,
    backend_label: str | None = None,
    base_version: GenerateVersion | None = None,
) -> tuple[GenerateHistory, GenerateVersion]:
    with transaction.atomic():
        locked = GenerateHistory.objects.select_for_update().get(pk=history.pk)
        last_no = locked.versions.aggregate(value=Max("version_no"))["value"] or 0
        version = GenerateVersion.objects.create(
            history=locked,
            version_no=last_no + 1,
            html_code=html_code,
            note=note,
            files=files or [],
            base_version=base_version,
        )
        locked.html_code = html_code
        fields = ["html_code"]
        if backend_label is not None:
            locked.backend_label = backend_label
            fields.append("backend_label")
        locked.save(update_fields=fields)
    history.refresh_from_db()
    return history, version


def _refine_working_message(elapsed: float) -> str:
    if elapsed < 5:
        return "正在理解修改建议与目标组件"
    if elapsed < 15:
        return "正在修改页面结构与样式"
    if elapsed < 35:
        return "正在同步项目文件与预览"
    return "正在检查修改结果并准备保存"


def _run_render_repair_job(job: GenerationJob) -> None:
    """Repair a generated preview that the browser reported as blank/broken."""
    state = job.state if isinstance(job.state, dict) else {}
    history = (
        GenerateHistory.objects.filter(pk=state.get("target_history_id"))
        .prefetch_related("versions")
        .first()
    )
    if not history:
        raise RuntimeError("项目不存在或已被删除，无法修复预览。")
    version = history.versions.filter(pk=state.get("target_version_id")).first()
    if not version:
        raise RuntimeError("目标版本不存在，无法修复预览。")

    issues = _normalize_preview_issues(state.get("issues"))
    provider = (
        history.model_provider if history.model_provider in PROVIDERS else "deepseek"
    )
    model_label = PROVIDERS[provider]["label"]
    state["kind"] = "render_repair"
    state["target_history_id"] = history.id
    state["target_version_id"] = version.id
    state["target_version_no"] = version.version_no
    state["target_project_prompt"] = history.prompt
    state["issues"] = issues
    state["providers"] = {
        provider: {
            "provider": provider,
            "label": model_label,
            "model": _provider_model(provider),
            "status": "streaming",
            "message": "首次生成的代码渲染失败，正在修复，请稍等",
            "chars": 0,
        }
    }
    state["stages"] = []
    repair_message = "首次生成的代码渲染失败，正在修复，请稍等"
    _job_stage(state, "preview_repair", "修复预览", repair_message)
    job.mode = "repair"
    job.status = GenerationJob.STATUS_RUNNING
    job.progress = 12
    job.stage_title = "修复预览"
    job.stage_message = repair_message
    job.project_type = history.project_type
    job.backend_label = history.backend_label
    job.state = state
    _save_job(job)

    prompt = _preview_repair_prompt(history, version, issues)
    events: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            result = _call_backend_model(
                provider,
                prompt,
                job_id=str(job.id),
                system_prompt=PREVIEW_REPAIR_SYSTEM_PROMPT,
            )
        except Exception:
            logger.exception("Preview repair call failed for %s.", provider)
            result = None
        events.put(result)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"job-{job.id}-preview-repair-{provider}",
    ).start()

    started_at = time.monotonic()
    last_save = time.monotonic()
    max_seconds = _backend_timeout(provider)
    content = None
    while True:
        _ensure_job_active(job.pk)
        try:
            content = events.get(timeout=1)
            break
        except queue.Empty:
            elapsed = time.monotonic() - started_at
            job.progress = max(
                job.progress,
                min(86, 16 + int(min(elapsed / max(max_seconds, 1), 1) * 68)),
            )
            if time.monotonic() - last_save >= 1.2:
                job.state = state
                _save_job(job)
                last_save = time.monotonic()

    _ensure_job_active(job.pk)
    if not content:
        raise RuntimeError("修复模型未返回结果，请稍后重试。")

    parsed = _parse_project_bundle(
        content,
        history.backend_label
        or ("原生 HTML/CSS/JavaScript" if history.project_type == "frontend" else "Django 4.2 + PostgreSQL"),
    )
    complete = (
        _frontend_project_is_complete(parsed)
        if history.project_type == "frontend"
        else _project_is_complete(parsed)
    )
    repaired_files = None
    repaired_label = None
    if parsed and complete and _preview_is_usable(parsed.get("preview_html")):
        preview = parsed["preview_html"]
        repaired_files = parsed["files"]
        repaired_label = parsed.get("backend_label") or None
    else:
        preview = _extract_repair_html(content)
        if not _preview_is_usable(preview):
            raise RuntimeError("修复模型返回的页面仍无法渲染，请稍后重试。")
        # Keep the project files and update the standalone preview copy only.
        repaired_files = None

    _ensure_job_active(job.pk)
    job.progress = 92
    job.stage_title = "保存修复结果"
    job.stage_message = "正在写入修复后的代码"
    job.state = state
    _save_job(job)
    history, version = _apply_preview_repair(
        history, version, preview, files=repaired_files, backend_label=repaired_label
    )
    _ensure_job_active(job.pk)
    _finish_job_stages(state)
    state["repair_applied"] = True
    state["created_version_id"] = version.id
    state["created_version_no"] = version.version_no
    state["providers"][provider]["status"] = "done"
    state["providers"][provider]["message"] = "预览修复完成"
    job.history = history
    job.status = GenerationJob.STATUS_COMPLETED
    job.progress = 100
    job.stage_title = "修复完成"
    job.stage_message = "修复后的页面已保存，正在重新检测预览"
    job.project_type = history.project_type
    job.backend_label = history.backend_label
    job.state = state
    _save_job(job)


def _run_refine_job(job: GenerationJob) -> None:
    state = job.state if isinstance(job.state, dict) else {}
    history = (
        GenerateHistory.objects.filter(pk=state.get("target_history_id"))
        .prefetch_related("versions")
        .first()
    )
    if not history:
        raise RuntimeError("项目不存在或已被删除，无法继续修改。")

    version_id = state.get("target_version_id")
    base_version = (
        history.versions.filter(pk=version_id).first()
        if version_id
        else history.versions.order_by("-version_no").first()
    )
    if not base_version:
        raise RuntimeError("目标版本不存在，无法继续修改。")

    instruction = str(state.get("instruction") or job.prompt or "").strip()
    if not instruction:
        raise RuntimeError("修改指令为空。")
    target_context = str(state.get("target_context") or "")
    target_label = str(state.get("target_label") or "")
    note = f"{target_label}｜{instruction}" if target_label else instruction
    base_html = _repair_babel_preview(base_version.html_code or "")
    base_files = base_version.files or []
    if not base_files and history.project_type in {"backend", "frontend"}:
        latest = history.versions.order_by("-version_no").first()
        base_files = latest.files if latest else []
    use_bundle = history.project_type in {"backend", "frontend"} and bool(base_files)
    frontend_bundle = use_bundle and history.project_type == "frontend"
    provider = (
        history.model_provider if history.model_provider in PROVIDERS else "deepseek"
    )
    provider_config = PROVIDERS[provider]
    model_label = provider_config["label"]

    if frontend_bundle:
        model_prompt = _frontend_refine_prompt(
            history.prompt, base_files, base_html, instruction, target_context
        )
    elif use_bundle:
        model_prompt = _backend_refine_prompt(
            history.prompt, base_files, base_html, instruction, target_context
        )
    else:
        model_prompt = _refine_prompt(base_html, instruction, target_context)

    state["kind"] = "refine"
    state["target_history_id"] = history.id
    state["target_version_id"] = base_version.id
    state["target_version_no"] = base_version.version_no
    state["target_project_prompt"] = history.prompt
    state["instruction"] = instruction
    state["target_context"] = target_context
    state["target_label"] = target_label
    state["used_mock"] = False
    state["race"] = None
    state["providers"] = {
        provider: {
            "provider": provider,
            "label": model_label,
            "model": _provider_model(provider),
            "status": "streaming",
            "message": "正在理解修改建议",
            "chars": 0,
        }
    }
    state["stages"] = []
    _job_stage(state, "prepare", "解析修改需求", "正在准备目标版本与修改上下文")
    _job_stage(
        state,
        "refine",
        "在线修改",
        f"正在修改项目 v{base_version.version_no}",
    )
    job.mode = "refine"
    job.status = GenerationJob.STATUS_RUNNING
    job.progress = 10
    job.stage_title = "在线修改"
    job.stage_message = f"{model_label} 正在修改项目 v{base_version.version_no}"
    job.state = state
    _save_job(job)

    events: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            if use_bundle:
                result = _call_backend_model(
                    provider,
                    model_prompt,
                    job_id=str(job.id),
                    system_prompt=(
                        FRONTEND_SYSTEM_PROMPT
                        if frontend_bundle
                        else BACKEND_SYSTEM_PROMPT
                    ),
                )
            else:
                result = _call_model(provider, model_prompt)
        except Exception:
            logger.exception("Background refine call failed for %s.", provider)
            result = None
        events.put(result)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"job-{job.id}-refine-{provider}",
    ).start()

    started_at = time.monotonic()
    last_save = time.monotonic()
    max_seconds = (
        _backend_timeout(provider) if use_bundle else _provider_timeout(provider)
    )
    result = None
    while True:
        _ensure_job_active(job.pk)
        try:
            result = events.get(timeout=1)
            break
        except queue.Empty:
            elapsed = time.monotonic() - started_at
            message = _refine_working_message(elapsed)
            provider_state = state["providers"][provider]
            provider_state["message"] = message
            job.progress = max(
                job.progress,
                min(90, 14 + int(min(elapsed / max(max_seconds, 1), 1) * 70)),
            )
            job.stage_message = f"{model_label}：{message}"
            if time.monotonic() - last_save >= 1.2:
                job.state = state
                _save_job(job)
                last_save = time.monotonic()

    _ensure_job_active(job.pk)
    if result is None:
        raise RuntimeError("模型未返回修改结果，请稍后重试。")

    _job_stage(state, "save", "保存修改", "正在写入新的项目版本")
    job.progress = max(job.progress, 94)
    job.stage_title = "保存修改"
    job.stage_message = "正在写入新的项目版本"
    job.state = state
    _save_job(job)
    _ensure_job_active(job.pk)

    previous_html = history.html_code
    previous_backend = history.backend_label
    created_version = None
    try:
        if use_bundle:
            parsed = _parse_project_bundle(
                result, history.backend_label or "前端项目"
            )
            is_complete = (
                _frontend_project_is_complete(parsed)
                if frontend_bundle
                else _project_is_complete(parsed)
            )
            if not parsed or not is_complete:
                raise RuntimeError("模型返回的项目文件不完整，未生成新版本。")
            history, created_version = _create_refine_version(
                history,
                parsed["preview_html"],
                note,
                files=parsed["files"],
                backend_label=parsed["backend_label"],
                base_version=base_version,
            )
            used_mock = False
        else:
            html_code, used_mock = result
            if not html_code:
                raise RuntimeError("模型返回的页面为空，未生成新版本。")
            history, created_version = _create_refine_version(
                history, html_code, note, base_version=base_version
            )

        _ensure_job_active(job.pk)
        _finish_job_stages(state)
        state["used_mock"] = used_mock
        state["created_version_id"] = created_version.id
        state["created_version_no"] = created_version.version_no
        state["providers"][provider]["status"] = "fallback" if used_mock else "done"
        state["providers"][provider]["message"] = (
            "调用失败，已使用 Mock 兜底"
            if used_mock
            else "修改完成"
        )
        job.history = history
        job.status = GenerationJob.STATUS_COMPLETED
        job.progress = 100
        job.stage_title = "修改完成"
        job.stage_message = f"已生成新版本 v{created_version.version_no}"
        job.project_type = history.project_type
        job.backend_label = history.backend_label
        job.state = state
        _save_job(job)
        _ensure_job_active(job.pk)
    except Exception:
        # Never leave a half-finished version behind when persisting the job
        # fails or the user cancels at the save boundary.
        if created_version is not None:
            try:
                created_version.delete()
                latest = (
                    GenerateHistory.objects.get(pk=history.pk)
                    .versions.order_by("-version_no")
                    .first()
                )
                GenerateHistory.objects.filter(pk=history.pk).update(
                    html_code=latest.html_code if latest else previous_html,
                    backend_label=previous_backend,
                )
            except Exception:
                logger.exception("Could not roll back refine version %s.", created_version.pk)
        raise


def _run_generation_job(job_id: str) -> None:
    close_old_connections()
    _register_job_cancel(job_id)
    job = None
    try:
        job = GenerationJob.objects.filter(pk=job_id).first()
        if not job:
            return
        state = job.state if isinstance(job.state, dict) else {}
        is_refine = state.get("kind") == "refine"
        is_repair = state.get("kind") == "render_repair"
        job.status = GenerationJob.STATUS_RUNNING
        job.stage_title = (
            "准备修复预览" if is_repair else "准备修改" if is_refine else "准备生成"
        )
        job.stage_message = (
            "首次生成的代码渲染失败，正在修复，请稍等"
            if is_repair
            else "后台修改任务已启动"
            if is_refine
            else "后台任务已启动"
        )
        _save_job(job)
        if is_refine:
            _run_refine_job(job)
        elif is_repair:
            _run_render_repair_job(job)
        elif job.project_type == "backend":
            _run_backend_job(job)
        elif state.get("bundle_mode") == "frontend":
            _run_backend_job(job, frontend_only=True)
        else:
            _run_frontend_job(job)
    except JobCancelled:
        logger.info("Generation job %s was deleted and stopped.", job_id)
    except Exception as exc:
        logger.exception("Background generation job %s failed.", job_id)
        try:
            _ensure_job_active(job_id)
            job = GenerationJob.objects.filter(pk=job_id).first()
            if job:
                state = job.state or {}
                is_refine = isinstance(state, dict) and state.get("kind") == "refine"
                _fail_job_stages(state)
                state["used_mock"] = False
                job.state = state
                job.status = GenerationJob.STATUS_FAILED
                job.error = str(exc)[:1000]
                job.stage_title = "修改失败" if is_refine else "生成失败"
                job.stage_message = (
                    "后台修改任务异常中止"
                    if is_refine
                    else "后台生成任务异常中止"
                )
                _save_job(job)
        except JobCancelled:
            logger.info("Generation job %s was deleted before failure could persist.", job_id)
        except Exception:
            logger.exception("Could not persist failed job %s.", job_id)
    finally:
        _unregister_job_cancel(job_id)
        close_old_connections()


@require_POST
def start_job(request):
    try:
        data = _read_json(request)
    except ValueError as exc:
        return _error(str(exc))

    prompt = data.get("prompt", "")
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    if not prompt:
        return _error("请输入生成需求。")
    if len(prompt) > MAX_PROMPT_LENGTH:
        return _error(f"提示词不能超过 {MAX_PROMPT_LENGTH} 个字符。")

    try:
        reference_image = _normalize_reference_image(data.get("image_data"))
        image_meta = _normalize_image_meta(data.get("image_meta"))
    except ValueError as exc:
        return _error(str(exc))

    requested_mode = data.get("mode", "single")
    if requested_mode not in {"single", "race"}:
        return _error("不支持的生成模式。")

    client_id = _normalize_client_id(data.get("client_id"))
    # Frontend-only is the default.  Generate a backend bundle only when the
    # user explicitly asks for a full-stack/backend project.
    frontend_only = not _wants_fullstack(prompt)
    if frontend_only:
        backend_label = _detect_frontend_label(prompt)
    else:
        _, detected_label = _detect_backend_requirement(prompt)
        backend_label = detected_label or "Django 4.2 + PostgreSQL"
    requested_provider = data.get("provider", "")
    requested_provider = (
        requested_provider.strip()
        if isinstance(requested_provider, str)
        else ""
    )
    if requested_provider and requested_provider not in PROVIDERS:
        return _error("不支持的模型。")
    selected_provider = None
    if requested_mode == "race":
        providers = list(RACE_PROVIDERS)
    else:
        selected_provider = requested_provider or _backend_provider()
        providers = [selected_provider]
    initial_state = {
        "mode": requested_mode,
        "bundle_mode": "frontend" if frontend_only else None,
        "has_reference_image": bool(reference_image),
        "reference_image_meta": image_meta,
        "single_provider": selected_provider,
        "order": providers,
        "providers": {
            provider: {
                "provider": provider,
                "label": PROVIDERS[provider]["label"],
                "model": _provider_model(provider),
                "status": "waiting",
                "message": "等待开始",
                "chars": 0,
                "backend": True,
            }
            for provider in providers
        },
        "stages": [
            {
                "key": "prepare",
                "title": "解析需求",
                "message": (
                    (
                        "任务已进入后台队列，将结合参考图生成前端项目"
                        if frontend_only
                        else "任务已进入后台队列，将结合参考图生成完整前后端项目"
                    )
                    if reference_image
                    else (
                        "任务已进入后台队列，将只生成前端项目"
                        if frontend_only
                        else "任务已进入后台队列，将生成完整前后端项目"
                    )
                ),
                "status": "active",
            }
        ],
        "used_mock": False,
        "race": None,
    }
    job = GenerationJob.objects.create(
        client_id=client_id,
        prompt=prompt,
        reference_image=reference_image,
        requested_mode=requested_mode,
        mode=requested_mode,
        status=GenerationJob.STATUS_QUEUED,
        progress=1,
        stage_title="任务排队",
        stage_message="已提交后台生成，可安全离开当前页面",
        state=initial_state,
        project_type="frontend" if frontend_only else "backend",
        backend_label=backend_label,
    )
    thread = threading.Thread(
        target=_run_generation_job, args=(str(job.id),), daemon=True,
        name=f"generation-job-{job.id}",
    )
    thread.start()
    return JsonResponse({"job": _job_payload(job)}, status=202)


@require_GET
def job_list(request):
    client_id = _normalize_client_id(request.GET.get("client_id"))
    jobs = (
        GenerationJob.objects.filter(client_id=client_id)
        .select_related("history")
        .order_by("-created_at")[:30]
    )
    return JsonResponse({"jobs": [_job_payload(job) for job in jobs]})


@require_GET
def job_detail(request, job_id):
    job = get_object_or_404(
        GenerationJob.objects.select_related("history"), pk=job_id
    )
    return JsonResponse({"job": _job_payload(job, request=request, include_history=True)})


@require_POST
def delete_job(request, job_id):
    job = GenerationJob.objects.select_related("history").filter(pk=job_id).first()
    if not job:
        return JsonResponse({"ok": True, "deleted": False, "cancelled": False})

    was_active = job.status in {
        GenerationJob.STATUS_QUEUED,
        GenerationJob.STATUS_RUNNING,
    }
    if was_active:
        _cancel_job_requests(str(job.id))
    try:
        # Re-read after signalling cancellation: the worker may have just
        # attached a history row, which must be removed as well when the user
        # deleted an in-flight task.
        job.refresh_from_db()
    except GenerationJob.DoesNotExist:
        return JsonResponse({"ok": True, "deleted": False, "cancelled": was_active})
    kind = (job.state or {}).get("kind")
    history_id = (
        job.history_id
        if (was_active or job.status != GenerationJob.STATUS_COMPLETED)
        and kind != "render_repair"
        else None
    )
    job.delete()
    if history_id:
        GenerateHistory.objects.filter(pk=history_id).delete()
    return JsonResponse({"ok": True, "deleted": True, "cancelled": was_active})


@require_POST
def start_preview_repair(request, history_id, version_id):
    """Queue a background repair when the browser detects a blank preview."""
    try:
        data = _read_json(request)
    except ValueError as exc:
        return _error(str(exc))

    history = (
        GenerateHistory.objects.prefetch_related("versions")
        .filter(pk=history_id)
        .first()
    )
    if not history:
        return _error("项目不存在或已被删除。", status=404)
    version = history.versions.filter(pk=version_id).first()
    if not version:
        return _error("版本不存在或已被删除。", status=404)

    client_id = _normalize_client_id(data.get("client_id"))
    issues = _normalize_preview_issues(data.get("issues"))
    try:
        attempt = int(data.get("attempt") or 1)
    except (TypeError, ValueError):
        attempt = 1
    attempt = max(1, min(attempt, MAX_PREVIEW_REPAIR_ATTEMPTS))

    # A browser may report the same failure repeatedly (page reload, two tabs).
    # Reuse an in-flight repair instead of paying for duplicate model calls.
    active_jobs = (
        GenerationJob.objects.filter(
            client_id=client_id,
            status__in=[GenerationJob.STATUS_QUEUED, GenerationJob.STATUS_RUNNING],
        )
        .order_by("-created_at")[:20]
    )
    for existing in active_jobs:
        existing_state = existing.state if isinstance(existing.state, dict) else {}
        if (
            existing_state.get("kind") == "render_repair"
            and existing_state.get("target_history_id") == history.id
            and existing_state.get("target_version_id") == version.id
        ):
            return JsonResponse({"job": _job_payload(existing)}, status=202)

    provider = (
        history.model_provider if history.model_provider in PROVIDERS else "deepseek"
    )
    state = {
        "kind": "render_repair",
        "target_history_id": history.id,
        "target_version_id": version.id,
        "target_version_no": version.version_no,
        "target_project_prompt": history.prompt,
        "issues": issues,
        "attempt": attempt,
        "providers": {
            provider: {
                "provider": provider,
                "label": PROVIDERS[provider]["label"],
                "model": _provider_model(provider),
                "status": "waiting",
                "message": "首次生成的代码渲染失败，正在修复，请稍等",
                "chars": 0,
            }
        },
        "stages": [
            {
                "key": "preview_repair",
                "title": "修复预览",
                "message": "首次生成的代码渲染失败，正在修复，请稍等",
                "status": "active",
            }
        ],
        "used_mock": False,
        "race": None,
    }
    job = GenerationJob.objects.create(
        client_id=client_id,
        prompt=history.prompt,
        requested_mode="single",
        mode="repair",
        status=GenerationJob.STATUS_QUEUED,
        progress=1,
        stage_title="预览修复排队",
        stage_message="首次生成的代码渲染失败，正在修复，请稍等",
        state=state,
        project_type=history.project_type,
        backend_label=history.backend_label,
    )
    threading.Thread(
        target=_run_generation_job,
        args=(str(job.id),),
        daemon=True,
        name=f"preview-repair-{job.id}",
    ).start()
    return JsonResponse({"job": _job_payload(job)}, status=202)


def _backend_event_stream(request, prompt: str, detected_label: str):
    started_at = time.monotonic()
    provider = _backend_provider()
    config = PROVIDERS[provider]
    backend_prompt = _backend_prompt(prompt, detected_label)
    provider_info = {
        "provider": provider,
        "label": config["label"],
        "model": _provider_model(provider),
        "backend": True,
    }
    yield _ndjson(
        {
            "type": "start",
            "mode": "backend",
            "project_type": "backend",
            "backend_label": detected_label,
            "providers": [provider_info],
        }
    )
    yield _ndjson(
        {
            "type": "stage",
            "key": "prepare",
            "title": "需求分析",
            "message": "检测到服务端需求，正在准备全栈项目生成",
            "progress": 4,
        }
    )
    yield _ndjson(
        {
            "type": "stage",
            "key": "backend",
            "title": "后端项目生成",
            "message": f"正在生成 {detected_label} 项目文件与前端预览",
            "progress": 12,
        }
    )

    events: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=_backend_worker,
        args=(provider, backend_prompt, events),
        daemon=True,
        name=f"backend-{provider}",
    )
    thread.start()
    content = None
    try:
        while content is None:
            try:
                event = events.get(timeout=8)
            except queue.Empty:
                elapsed = time.monotonic() - started_at
                progress = min(82, 14 + int(elapsed / max(_backend_timeout(provider), 1) * 68))
                yield _ndjson(
                    {
                        "type": "stage",
                        "key": "backend",
                        "title": "后端项目生成",
                        "message": f"正在生成 {detected_label} 项目文件与前端预览 · 已用时 {elapsed:.0f}s",
                        "progress": progress,
                    }
                )
                yield _ndjson(
                    {"type": "heartbeat", "elapsed": round(elapsed, 1)}
                )
                continue
            if event.get("type") == "__backend_result":
                content = event.get("content")
                break
            yield _ndjson(event)

        if not content:
            yield _ndjson(
                {
                    "type": "provider",
                    "provider": provider,
                    "label": config["label"],
                    "model": _provider_model(provider),
                    "status": "fallback",
                    "backend": True,
                    "message": "后端项目生成失败，已使用 Mock 兜底",
                }
            )
            yield _ndjson(
                {
                    "type": "stage",
                    "key": "fallback",
                    "title": "Mock 兜底",
                    "message": "后端项目生成失败，正在保存可交互的兜底页面",
                    "progress": 90,
                }
            )
            history = _create_history(
                prompt,
                MOCK_HTML,
                "后端项目生成失败 · Mock 兜底",
                provider=provider,
            )
            yield _ndjson(
                {
                    "type": "stage",
                    "key": "save",
                    "title": "保存项目",
                    "message": "项目已保存",
                    "progress": 96,
                }
            )
            yield _ndjson(
                {
                    "type": "done",
                    "mode": "backend",
                    "history": _history_payload(
                        history, include_html=True, request=request
                    ),
                    "used_mock": True,
                    "elapsed": round(time.monotonic() - started_at, 1),
                }
            )
            return

        yield _ndjson(
            {
                "type": "stage",
                "key": "files",
                "title": "整理项目文件",
                "message": "正在解析后端代码、页面预览与目录结构",
                "progress": 86,
            }
        )
        parsed = _parse_project_bundle(content, detected_label)
        if not parsed:
            yield _ndjson(
                {
                    "type": "provider",
                    "provider": provider,
                    "label": config["label"],
                    "model": _provider_model(provider),
                    "status": "fallback",
                    "backend": True,
                    "message": "项目文件格式不完整，已使用 Mock 兜底",
                }
            )
            yield _ndjson(
                {
                    "type": "stage",
                    "key": "fallback",
                    "title": "Mock 兜底",
                    "message": "项目文件解析失败，正在保存可交互的兜底页面",
                    "progress": 90,
                }
            )
            history = _create_history(
                prompt,
                MOCK_HTML,
                "后端项目解析失败 · Mock 兜底",
                provider=provider,
            )
            yield _ndjson(
                {
                    "type": "stage",
                    "key": "save",
                    "title": "保存项目",
                    "message": "项目已保存",
                    "progress": 96,
                }
            )
            yield _ndjson(
                {
                    "type": "done",
                    "mode": "backend",
                    "history": _history_payload(
                        history, include_html=True, request=request
                    ),
                    "used_mock": True,
                    "elapsed": round(time.monotonic() - started_at, 1),
                }
            )
            return

        yield _ndjson(
            {
                "type": "provider",
                "provider": provider,
                "label": config["label"],
                "model": _provider_model(provider),
                "status": "done",
                "backend": True,
                "message": f"已生成 {len(parsed['files'])} 个项目文件",
                "chars": sum(len(item["content"]) for item in parsed["files"]),
            }
        )
        yield _ndjson(
            {
                "type": "stage",
                "key": "save",
                "title": "保存项目",
                "message": "正在写入项目文件、预览和首个版本",
                "progress": 96,
            }
        )
        history = _create_history(
            prompt,
            parsed["preview_html"],
            f"全栈项目 · {parsed['backend_label']}",
            provider=provider,
            files=parsed["files"],
            project_type="backend",
            backend_label=parsed["backend_label"],
        )
        yield _ndjson(
            {
                "type": "done",
                "mode": "backend",
                "history": _history_payload(
                    history, include_html=True, request=request
                ),
                "used_mock": False,
                "elapsed": round(time.monotonic() - started_at, 1),
            }
        )
    except Exception as exc:
        logger.exception("Backend streaming generation failed.")
        yield _ndjson({"type": "error", "message": f"全栈项目生成失败：{exc}"})


@require_POST
def generate_stream(request):
    try:
        data = _read_json(request)
    except ValueError as exc:
        return _error(str(exc))

    prompt = data.get("prompt", "")
    prompt = prompt.strip() if isinstance(prompt, str) else ""
    if not prompt:
        return _error("请输入生成需求。")
    if len(prompt) > MAX_PROMPT_LENGTH:
        return _error(f"提示词不能超过 {MAX_PROMPT_LENGTH} 个字符。")

    backend_required, backend_label = _detect_backend_requirement(prompt)
    if backend_required:
        response = StreamingHttpResponse(
            _backend_event_stream(request, prompt, backend_label),
            content_type="application/x-ndjson; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["X-Accel-Buffering"] = "no"
        return response

    mode = data.get("mode", "single")
    if mode not in {"single", "race"}:
        return _error("不支持的生成模式。")

    providers = list(RACE_PROVIDERS) if mode == "race" else ["deepseek"]
    prompt_text = _single_prompt(prompt)

    def event_stream():
        started_at = time.monotonic()
        yield _ndjson(
            {
                "type": "start",
                "mode": mode,
                "providers": [
                    {
                        "provider": provider,
                        "label": PROVIDERS[provider]["label"],
                        "model": _provider_model(provider),
                    }
                    for provider in providers
                ],
            }
        )
        yield _ndjson(
            {
                "type": "stage",
                "key": "prepare",
                "title": "解析需求",
                "message": "提示词已接收，正在准备模型请求",
                "progress": 4,
            }
        )

        events: queue.Queue = queue.Queue()
        threads = []
        for provider in providers:
            thread = threading.Thread(
                target=_model_stream_worker,
                args=(provider, prompt_text, events),
                daemon=True,
                name=f"generate-{provider}",
            )
            thread.start()
            threads.append(thread)

        yield _ndjson(
            {
                "type": "stage",
                "key": "generate",
                "title": "模型生成",
                "message": (
                    "DeepSeek 与千问正在并行输出页面"
                    if mode == "race"
                    else "DeepSeek 正在输出页面"
                ),
                "progress": 18,
            }
        )

        results: dict[str, dict] = {}
        pending = set(providers)
        total_chars = 0
        last_progress_at = time.monotonic()

        try:
            while pending:
                try:
                    event = events.get(timeout=10)
                except queue.Empty:
                    yield _ndjson(
                        {
                            "type": "heartbeat",
                            "elapsed": round(time.monotonic() - started_at, 1),
                        }
                    )
                    continue

                if event.get("type") == "__result":
                    results[event["provider"]] = event["result"]
                    pending.discard(event["provider"])
                    continue

                if event.get("type") == "delta":
                    total_chars += int(event.get("chars") or 0)
                yield _ndjson(event)

                now = time.monotonic()
                if total_chars and now - last_progress_at >= 0.75:
                    progress = 20 + int(min(total_chars, 25000) / 25000 * 62)
                    yield _ndjson(
                        {
                            "type": "progress",
                            "progress": progress,
                            "chars": total_chars,
                        }
                    )
                    last_progress_at = now

            if mode == "race":
                yield _ndjson(
                    {
                        "type": "stage",
                        "key": "score",
                        "title": "网页质量评审",
                        "message": "正在按十维评分标准比较两个模型的结果",
                        "progress": 88,
                    }
                )
                models = [
                    _race_model_payload(
                        provider,
                        results[provider]["html_code"],
                        results[provider]["is_mock"],
                        prompt,
                    )
                    for provider in providers
                ]
                winner, tied = _pick_race_winner(models)
                winner_html = results[winner["provider"]]["html_code"]
                note = _race_note(models, winner, tied)
                used_mock = any(model["is_mock"] for model in models)
            else:
                models = []
                winner = None
                tied = False
                result = results["deepseek"]
                winner_html = result["html_code"]
                note = "初始生成"
                used_mock = result["is_mock"]

            yield _ndjson(
                {
                    "type": "stage",
                    "key": "save",
                    "title": "保存项目",
                    "message": "正在写入数据库并创建首个版本",
                    "progress": 96,
                }
            )
            history = _create_history(
                prompt,
                winner_html,
                note,
                provider=winner["provider"] if mode == "race" else "deepseek",
            )
            done_payload = {
                "type": "done",
                "mode": mode,
                "history": _history_payload(
                    history, include_html=True, request=request
                ),
                "used_mock": used_mock,
                "elapsed": round(time.monotonic() - started_at, 1),
            }
            if mode == "race":
                done_payload.update(
                    {"models": models, "winner": winner, "tied": tied}
                )
            yield _ndjson(done_payload)
        except Exception as exc:
            logger.exception("Streaming generation failed.")
            yield _ndjson({"type": "error", "message": f"生成失败：{exc}"})

    response = StreamingHttpResponse(
        event_stream(), content_type="application/x-ndjson; charset=utf-8"
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def refine(request, history_id: int):
    try:
        data = _read_json(request)
    except ValueError as exc:
        return _error(str(exc))

    instruction = data.get("instruction", "")
    instruction = instruction.strip() if isinstance(instruction, str) else ""
    if not instruction:
        return _error("请输入修改建议。")
    if len(instruction) > MAX_INSTRUCTION_LENGTH:
        return _error(f"修改建议不能超过 {MAX_INSTRUCTION_LENGTH} 个字符。")

    target_context = data.get("target_context", "")
    target_context = target_context.strip() if isinstance(target_context, str) else ""
    target_context = target_context[:5000]
    target_label = data.get("target_label", "")
    target_label = target_label.strip() if isinstance(target_label, str) else ""
    target_label = target_label[:300]

    history = get_object_or_404(GenerateHistory, pk=history_id)
    base_version = None
    version_id = data.get("version_id")
    if version_id not in (None, ""):
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            return _error("版本参数不正确。")
        base_version = history.versions.filter(pk=version_id).first()
        if not base_version:
            return _error("指定版本不存在。", 404)

    if bool(data.get("background")):
        if base_version is None:
            base_version = history.versions.order_by("-version_no").first()
        if base_version is None:
            return _error("项目还没有可修改的版本。", 409)
        provider = (
            history.model_provider
            if history.model_provider in PROVIDERS
            else "deepseek"
        )
        client_id = _normalize_client_id(data.get("client_id"))
        target_version_no = base_version.version_no
        initial_state = {
            "kind": "refine",
            "target_history_id": history.id,
            "target_version_id": base_version.id,
            "target_version_no": target_version_no,
            "target_project_prompt": history.prompt,
            "instruction": instruction,
            "target_context": target_context,
            "target_label": target_label,
            "providers": {
                provider: {
                    "provider": provider,
                    "label": PROVIDERS[provider]["label"],
                    "model": _provider_model(provider),
                    "status": "waiting",
                    "message": "等待开始",
                    "chars": 0,
                }
            },
            "stages": [
                {
                    "key": "prepare",
                    "title": "修改排队",
                    "message": "任务已进入后台队列，将修改指定版本",
                    "status": "active",
                }
            ],
            "used_mock": False,
            "race": None,
        }
        job = GenerationJob.objects.create(
            client_id=client_id,
            prompt=instruction,
            requested_mode="refine",
            mode="refine",
            status=GenerationJob.STATUS_QUEUED,
            progress=1,
            stage_title="修改排队",
            stage_message=f"准备修改 v{target_version_no}",
            state=initial_state,
            project_type=history.project_type,
            backend_label=history.backend_label,
        )
        threading.Thread(
            target=_run_generation_job,
            args=(str(job.id),),
            daemon=True,
            name=f"refine-job-{job.id}",
        ).start()
        return JsonResponse({"job": _job_payload(job)}, status=202)

    # Refining an older version bases the change on that snapshot, not silently
    # on the newest HTML.
    provider = history.model_provider if history.model_provider in PROVIDERS else "deepseek"
    base_html = _repair_babel_preview(
        base_version.html_code if base_version else history.html_code
    )
    base_files = base_version.files if base_version else []
    if not base_files and history.project_type in {"backend", "frontend"}:
        latest = history.versions.order_by("-version_no").first()
        base_files = latest.files if latest else []
    note = f"{target_label}｜{instruction}" if target_label else instruction
    frontend_bundle = history.project_type == "frontend" and bool(base_files)
    use_bundle = history.project_type in {"backend", "frontend"} and bool(base_files)

    if use_bundle:
        if frontend_bundle:
            model_prompt = _frontend_refine_prompt(
                history.prompt, base_files, base_html, instruction, target_context
            )
            system_prompt = FRONTEND_SYSTEM_PROMPT
        else:
            model_prompt = _backend_refine_prompt(
                history.prompt, base_files, base_html, instruction, target_context
            )
            system_prompt = BACKEND_SYSTEM_PROMPT
        content = _call_backend_model(
            provider, model_prompt, system_prompt=system_prompt
        )
        parsed = _parse_project_bundle(
            content or "", history.backend_label or "项目"
        )
        is_complete = (
            _frontend_project_is_complete(parsed)
            if frontend_bundle
            else _project_is_complete(parsed)
        )
        if not parsed or not is_complete:
            return _error("项目修改失败，模型返回的文件不完整，请保留当前版本后重试。", 502)
        history = _create_backend_version(
            history,
            parsed["files"],
            parsed["preview_html"],
            note,
            parsed["backend_label"],
            base_version=base_version,
        )
        return JsonResponse(
            {
                "history": _history_payload(
                    history, include_html=True, request=request
                ),
                "used_mock": False,
            },
            status=201,
        )

    html_code, used_mock = _call_model(
        provider, _refine_prompt(base_html, instruction, target_context)
    )

    with transaction.atomic():
        locked = GenerateHistory.objects.select_for_update().get(pk=history_id)
        last_no = locked.versions.aggregate(value=Max("version_no"))["value"] or 0
        GenerateVersion.objects.create(
            history=locked,
            version_no=last_no + 1,
            html_code=html_code,
            note=note,
            base_version=base_version,
        )
        locked.html_code = html_code
        locked.save(update_fields=["html_code"])

    history.refresh_from_db()
    return JsonResponse(
        {
            "history": _history_payload(
                history, include_html=True, request=request
            ),
            "used_mock": used_mock,
        },
        status=201,
    )


def _backend_refine_prompt(
    prompt: str,
    files: list,
    preview_html: str,
    instruction: str,
    target_context: str = "",
) -> str:
    file_blocks = "\n".join(
        f"===FILE: {item.get('path', '')}===\n{item.get('content', '')}\n===END_FILE==="
        for item in files
    )
    target_block = (
        f"\n本次修改优先针对以下组件/局部：\n{target_context}\n"
        if target_context else ""
    )
    return (
        "用户项目需求：\n"
        f"{prompt}\n\n"
        "当前项目文件：\n"
        f"{file_blocks}\n\n"
        "当前静态预览：\n===PREVIEW_HTML===\n"
        f"{preview_html}\n"
        "===END_PREVIEW===\n\n"
        f"用户修改要求：{instruction}\n"
        f"{target_block}\n"
        "请基于当前项目完整更新，保持原技术栈并修复前后端之间的必要契约；"
        "输出修改后的完整项目文件包和更新后的 PREVIEW_HTML，必须使用同样的分段格式。"
    )


def _frontend_refine_prompt(
    prompt: str,
    files: list,
    preview_html: str,
    instruction: str,
    target_context: str = "",
) -> str:
    file_blocks = "\n".join(
        f"===FILE: {item.get('path', '')}===\n{item.get('content', '')}\n===END_FILE==="
        for item in files
    )
    target_block = (
        f"\n本次修改优先针对以下组件/局部：\n{target_context}\n"
        if target_context else ""
    )
    return (
        "用户前端项目需求：\n"
        f"{prompt}\n\n"
        "当前前端项目文件：\n"
        f"{file_blocks}\n\n"
        "当前静态预览：\n===PREVIEW_HTML===\n"
        f"{preview_html}\n"
        "===END_PREVIEW===\n\n"
        f"用户修改要求：{instruction}\n"
        f"{target_block}\n"
        "请只更新前端代码，保持原有前端技术栈和目录结构，不要引入后端、数据库或服务器代码；"
        "输出修改后的完整前端文件包和更新后的 PREVIEW_HTML，必须使用同样的分段格式。"
    )


def _create_backend_version(
    history: GenerateHistory,
    files: list,
    preview_html: str,
    note: str,
    backend_label: str,
    base_version: GenerateVersion | None = None,
) -> GenerateHistory:
    with transaction.atomic():
        locked = GenerateHistory.objects.select_for_update().get(pk=history.pk)
        last_no = locked.versions.aggregate(value=Max("version_no"))["value"] or 0
        GenerateVersion.objects.create(
            history=locked,
            version_no=last_no + 1,
            html_code=preview_html,
            note=note,
            files=files,
            base_version=base_version,
        )
        locked.html_code = preview_html
        locked.backend_label = backend_label
        locked.save(update_fields=["html_code", "backend_label"])
    history.refresh_from_db()
    return history


@require_GET
def download_version(request, history_id: int, version_id: int):
    version = get_object_or_404(
        GenerateVersion, pk=version_id, history_id=history_id
    )
    preview_html = _repair_babel_preview(version.html_code)
    if version.files:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            written = set()
            for item in version.files:
                path = _safe_project_path(str(item.get("path", "")))
                content = item.get("content", "")
                if not path or path in written or not isinstance(content, str):
                    continue
                archive.writestr(path, content)
                written.add(path)
            if preview_html and "preview.html" not in written:
                archive.writestr("preview.html", preview_html)
        response = HttpResponse(buffer.getvalue(), content_type="application/zip")
        filename = f"promptforge-project-{history_id}-v{version.version_no}.zip"
    else:
        response = HttpResponse(
            preview_html, content_type="text/html; charset=utf-8"
        )
        filename = f"promptforge-project-{history_id}-v{version.version_no}.html"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_POST
def publish_version(request, history_id: int, version_id: int):
    version = get_object_or_404(
        GenerateVersion, pk=version_id, history_id=history_id
    )
    publication = PublishedPage.objects.filter(version=version).first()
    if publication is None:
        slug = secrets.token_urlsafe(9)
        while PublishedPage.objects.filter(slug=slug).exists():
            slug = secrets.token_urlsafe(9)
        publication = PublishedPage.objects.create(version=version, slug=slug)
    return JsonResponse(
        {
            "published": True,
            "publication": _publication_payload(publication, request),
        }
    )


@require_POST
def unpublish_version(request, history_id: int, version_id: int):
    get_object_or_404(
        GenerateVersion, pk=version_id, history_id=history_id
    )
    deleted, _ = PublishedPage.objects.filter(version_id=version_id).delete()
    return JsonResponse({"unpublished": bool(deleted)})


@require_POST
def delete_version(request, history_id: int, version_id: int):
    with transaction.atomic():
        history = get_object_or_404(
            GenerateHistory.objects.select_for_update(), pk=history_id
        )
        version = get_object_or_404(
            GenerateVersion, pk=version_id, history_id=history_id
        )
        version.delete()
        if not history.versions.exists():
            history.delete()
            return JsonResponse({"deleted": True, "history_deleted": True})

        latest = history.versions.order_by("-version_no").first()
        history.html_code = latest.html_code
        history.save(update_fields=["html_code"])

    updated = get_object_or_404(
        GenerateHistory.objects.prefetch_related("versions__publication"),
        pk=history_id,
    )
    return JsonResponse(
        {
            "deleted": True,
            "history_deleted": False,
            "history": _history_payload(updated, request=request),
        }
    )


def _normalize_source_files(value) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("源码文件列表不能为空。")
    files = []
    seen = set()
    total_size = 0
    for item in value[:100]:
        if not isinstance(item, dict):
            raise ValueError("源码文件格式不正确。")
        path = _safe_project_path(str(item.get("path", "")))
        content = item.get("content")
        if not path or path in seen or not isinstance(content, str):
            raise ValueError("源码文件路径或内容不正确。")
        if path == "preview.html":
            continue
        total_size += len(content)
        if total_size > 4 * 1024 * 1024:
            raise ValueError("源码总大小超过 4MB，请减少文件内容。")
        seen.add(path)
        files.append({"path": path, "content": content})
    if not files:
        raise ValueError("源码文件列表不能为空。")
    return files


@require_POST
def save_version_source(request, history_id: int, version_id: int):
    try:
        data = _read_json(request)
        files = _normalize_source_files(data.get("files"))
        html_code = data.get("html_code", "")
        if not isinstance(html_code, str) or len(html_code) > 4 * 1024 * 1024:
            return _error("预览 HTML 格式不正确或超过 4MB。")
    except ValueError as exc:
        return _error(str(exc))

    version = get_object_or_404(
        GenerateVersion, pk=version_id, history_id=history_id
    )
    history = version.history
    if not html_code:
        html_code = version.html_code
    version.files = files
    version.html_code = html_code
    version.save(update_fields=["files", "html_code"])
    latest_id = history.versions.order_by("-version_no").values_list("id", flat=True).first()
    if latest_id == version.id:
        history.html_code = html_code
        history.save(update_fields=["html_code"])
    return JsonResponse(
        {
            "version": _version_payload(version, include_html=True, request=request),
            "history_html_updated": latest_id == version.id,
        }
    )


@require_POST
def delete_history(request, history_id: int):
    history = get_object_or_404(GenerateHistory, pk=history_id)
    version_count = history.versions.count()
    published_count = PublishedPage.objects.filter(
        version__history_id=history_id
    ).count()
    history.delete()
    return JsonResponse(
        {
            "deleted": True,
            "versions_deleted": version_count,
            "published_deleted": published_count,
        }
    )


SANDBOX_STORAGE_SHIM = """<script>
(function () {
  // Published pages deliberately run on an opaque origin (CSP sandbox), so
  // localStorage/sessionStorage throw and cookies/window.name do not survive
  // a refresh. Keep the synchronous storage facade in memory, and mirror it
  // into a reserved query parameter via history.replaceState; the URL (and
  // therefore the saved state) is restored on refresh and bookmark.
  var PARAM = "__pf_store";
  var MAX_STATE_CHARS = 32000;
  var namespace = (function () {
    var match = location.pathname.match(/^\\/p\\/[^\\/]+\\//);
    return match ? match[0] : (location.pathname || "published");
  })();
  function normalizeStore(store) {
    var out = Object.create(null);
    if (store && typeof store === "object") {
      Object.keys(store).forEach(function (key) { out[key] = String(store[key]); });
    }
    return out;
  }
  function readEnvelope() {
    try {
      var raw = new URLSearchParams(location.search).get(PARAM);
      if (raw) {
        var parsed = JSON.parse(raw);
        if (parsed && parsed.namespace === namespace && parsed.stores) {
          return {
            namespace: namespace,
            stores: {
              local: normalizeStore(parsed.stores.local),
              session: normalizeStore(parsed.stores.session)
            }
          };
        }
      }
    } catch (error) {}
    return {
      namespace: namespace,
      stores: { local: Object.create(null), session: Object.create(null) }
    };
  }
  var envelope = readEnvelope();
  function persist() {
    try {
      var serialized = JSON.stringify(envelope);
      if (serialized.length > MAX_STATE_CHARS) return;
      var url = new URL(location.href);
      if (Object.keys(envelope.stores.local).length || Object.keys(envelope.stores.session).length) {
        url.searchParams.set(PARAM, serialized);
      } else {
        url.searchParams.delete(PARAM);
      }
      history.replaceState(null, "", url.toString());
    } catch (error) {}
  }
  function makeStorage(kind) {
    var data = envelope.stores[kind];
    function keys() { return Object.keys(data); }
    return {
      get length() { return keys().length; },
      clear: function () {
        envelope.stores[kind] = Object.create(null);
        data = envelope.stores[kind];
        persist();
      },
      getItem: function (key) {
        key = String(key);
        return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
      },
      setItem: function (key, value) {
        data[String(key)] = String(value);
        persist();
      },
      removeItem: function (key) {
        delete data[String(key)];
        persist();
      },
      key: function (index) {
        var all = keys(); return index >= 0 && index < all.length ? all[index] : null;
      }
    };
  }
  function install(name, kind) {
    try {
      var current = window[name];
      var probe = "__promptforge_storage_probe__";
      current.setItem(probe, "1");
      current.removeItem(probe);
      return;
    } catch (error) {}
    var fallback = makeStorage(kind);
    try {
      Object.defineProperty(window, name, { configurable: true, value: fallback });
    } catch (error) {
      try { window[name] = fallback; } catch (ignored) {}
    }
  }
  install("localStorage", "local");
  install("sessionStorage", "session");
})();
</script>"""


def _inject_sandbox_storage(html_code: str) -> str:
    head = re.search(r"<head[^>]*>", html_code, flags=re.IGNORECASE)
    if head:
        index = head.end()
        return html_code[:index] + SANDBOX_STORAGE_SHIM + html_code[index:]
    first_script = re.search(r"<script", html_code, flags=re.IGNORECASE)
    if first_script:
        index = first_script.start()
        return html_code[:index] + SANDBOX_STORAGE_SHIM + html_code[index:]
    body = re.search(r"<body", html_code, flags=re.IGNORECASE)
    if body:
        index = body.start()
        return html_code[:index] + SANDBOX_STORAGE_SHIM + html_code[index:]
    return SANDBOX_STORAGE_SHIM + html_code


@require_GET
def published_page(request, slug: str):
    publication = get_object_or_404(
        PublishedPage.objects.select_related("version"), slug=slug
    )
    response = HttpResponse(
        _inject_sandbox_storage(
            _repair_babel_preview(publication.version.html_code)
        ),
        content_type="text/html; charset=utf-8",
    )
    # Keep the deployed page on an opaque origin: interactive scripts still run,
    # but generated code cannot read the host site's cookies or storage.
    response["Content-Security-Policy"] = (
        "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads"
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response
