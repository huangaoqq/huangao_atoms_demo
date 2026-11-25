"""DeepSeek 调用、Mock 兜底和记录序列化。"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from django.conf import settings
from django.utils import timezone

from .models import GenerateHistory

logger = logging.getLogger(__name__)

# 题目指定的固定前端 system prompt，不在代码中随意改动。
FRONTEND_SYSTEM_PROMPT = (
    "你是前端工程师，只输出完整可独立运行的单文件HTML，"
    "里面JS可以交互，不要额外解释、不要markdown代码块标记。"
)

# 后端代码与前端 HTML 分开生成，避免破坏上面的固定前端 system prompt。
BACKEND_SYSTEM_PROMPT = (
    "你是后端工程师，只输出完整可独立运行的后端代码，"
    "不要额外解释、不要markdown代码块标记。"
)

LLM_TIMEOUT_SECONDS = 30


class LLMError(RuntimeError):
    """模型不可用或返回内容不合法。"""


# ---------------------------------------------------------------------------
# Mock 兜底：可交互加法计算器
# ---------------------------------------------------------------------------

MOCK_HTML_SIMPLE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>加法计算器 · Mock</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
  background:
    radial-gradient(circle at 12% 14%, rgba(28, 61, 104, .17), transparent 32%),
    radial-gradient(circle at 88% 82%, rgba(128, 38, 55, .12), transparent 34%),
    #ebe4d6;
  color: #1b2d45;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.card {
  width: min(460px, 100%); padding: 42px; background: #fbf8f1;
  border: 1px solid #d8cbb6; border-radius: 22px;
  box-shadow: 0 28px 70px rgba(32, 44, 60, .14);
}
.eyebrow {
  display: inline-block; margin-bottom: 18px; padding: 6px 10px;
  border-radius: 999px; background: #e9dfca; color: #7d2c38;
  font-size: 11px; font-weight: 700; letter-spacing: 1.4px;
}
h1 { margin: 0; font-family: Georgia, "Songti SC", "SimSun", serif; color: #193a63; font-size: 34px; font-weight: 600; letter-spacing: -1.2px; }
.lead { margin: 10px 0 28px; color: #6d6456; font-size: 14px; line-height: 1.6; }
.fields { display: grid; grid-template-columns: 1fr 24px 1fr; align-items: end; gap: 10px; }
label { display: grid; gap: 8px; color: #6e6253; font-size: 12px; font-weight: 700; }
input {
  width: 100%; padding: 14px 15px; border: 1px solid #d8ccb7; border-radius: 12px;
  background: #f7f2e7; color: #172a44; font: inherit; font-size: 20px;
  outline: none; transition: border-color .2s, box-shadow .2s;
}
input:focus { border-color: #8f2d3c; box-shadow: 0 0 0 4px rgba(143, 45, 60, .10); }
.plus { padding-bottom: 16px; text-align: center; color: #a77b2f; font-size: 22px; }
button {
  width: 100%; margin-top: 18px; padding: 15px 18px; border: 0; border-radius: 12px;
  background: #1c3d68; color: #fdf8ee; cursor: pointer; font: inherit; font-weight: 700;
  transition: transform .15s, background .2s;
}
button:hover { background: #274e7f; transform: translateY(-1px); }
button:active { transform: translateY(1px); }
.result {
  display: flex; align-items: end; justify-content: space-between;
  margin-top: 22px; padding-top: 20px; border-top: 1px solid #e2d7c4;
}
.result span { color: #8a6a2f; font-size: 11px; font-weight: 800; letter-spacing: 1.5px; }
output { color: #7e2938; font-size: 46px; font-weight: 700; letter-spacing: -2px; line-height: 1; }
.hint { margin: 18px 0 0; color: #998c78; font-size: 12px; text-align: center; }
</style>
</head>
<body>
<main class="card">
  <div class="eyebrow">RESTRAINED · FALLBACK</div>
  <h1>加法计算器</h1>
  <p class="lead">大模型暂不可用，已切换为庄重内敛风格的本地 Mock。功能依旧可以交互。</p>
  <div class="fields">
    <label>数字 A<input id="numA" type="number" step="any" value="12" aria-label="数字 A"></label>
    <span class="plus">+</span>
    <label>数字 B<input id="numB" type="number" step="any" value="30" aria-label="数字 B"></label>
  </div>
  <button id="calculate" type="button">计算结果</button>
  <div class="result"><span>SUM</span><output id="result">42</output></div>
  <p class="hint">支持小数 · 按 Enter 快速计算</p>
</main>
<script>
(function () {
  var a = document.getElementById('numA');
  var b = document.getElementById('numB');
  var output = document.getElementById('result');
  var button = document.getElementById('calculate');

  function formatNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(10)));
  }

  function calculate() {
    var x = Number(a.value);
    var y = Number(b.value);
    output.textContent = Number.isFinite(x) && Number.isFinite(y) ? formatNumber(x + y) : '—';
    if (output.animate) {
      output.animate(
        [{ transform: 'translateY(4px)', opacity: .45 }, { transform: 'translateY(0)', opacity: 1 }],
        { duration: 180, easing: 'ease-out' }
      );
    }
  }

  button.addEventListener('click', calculate);
  [a, b].forEach(function (input) {
    input.addEventListener('input', calculate);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') calculate();
    });
  });
  calculate();
})();
</script>
</body>
</html>
"""

MOCK_HTML_RICH = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sum Studio · Mock</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
  background:
    radial-gradient(circle at 12% 16%, rgba(255, 78, 139, .50), transparent 31%),
    radial-gradient(circle at 88% 78%, rgba(255, 196, 61, .34), transparent 34%),
    radial-gradient(circle at 52% 48%, rgba(52, 211, 235, .16), transparent 46%),
    #1a0916;
  color: #f8f7ff; font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  overflow-x: hidden;
}
body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; opacity: .25;
  background-image: linear-gradient(rgba(255,255,255,.08) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,255,255,.08) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(circle at center, #000, transparent 75%);
}
.shell {
  position: relative; width: min(640px, 100%); padding: 2px; border-radius: 28px;
  background: linear-gradient(140deg, rgba(255, 143, 171, .90), rgba(255, 209, 102, .55) 48%, rgba(52, 211, 235, .32));
  box-shadow: 0 40px 100px rgba(0, 0, 0, .52);
}
.card {
  position: relative; overflow: hidden; padding: 36px; border-radius: 26px;
  background: rgba(27, 11, 24, .92); backdrop-filter: blur(18px);
}
.orb {
  position: absolute; width: 220px; height: 220px; top: -125px; right: -70px;
  border-radius: 50%; background: #ff4f8b; filter: blur(60px); opacity: .55;
  pointer-events: none;
}
.top { display: flex; align-items: start; justify-content: space-between; gap: 20px; }
.badge {
  display: inline-flex; align-items: center; gap: 7px; color: #ffd166;
  font-size: 11px; font-weight: 800; letter-spacing: 1.8px;
}
.badge i { width: 7px; height: 7px; border-radius: 50%; background: #ff4f8b; box-shadow: 0 0 12px #ff4f8b; }
h1 { margin: 14px 0 0; font-size: clamp(32px, 7vw, 52px); line-height: .98; letter-spacing: -2.3px; }
h1 span { color: #ffb703; }
.caption { margin: 12px 0 0; max-width: 380px; color: #9693a7; font-size: 13px; line-height: 1.7; }
.mark {
  flex: 0 0 auto; width: 50px; height: 50px; display: grid; place-items: center;
  border: 1px solid rgba(255,255,255,.12); border-radius: 15px;
  background: rgba(255,255,255,.04); color: #ffd166; font-size: 24px;
}
.calculator { margin-top: 30px; padding: 19px; border: 1px solid rgba(255,255,255,.09); border-radius: 20px; background: rgba(6, 6, 12, .38); }
.inputs { display: grid; grid-template-columns: 1fr 32px 1fr; align-items: end; gap: 8px; }
.field { display: grid; gap: 8px; }
.field span { color: #777488; font-size: 10px; font-weight: 800; letter-spacing: 1.5px; }
input {
  width: 100%; min-width: 0; padding: 15px 14px; border: 1px solid rgba(255,255,255,.1);
  border-radius: 13px; outline: none; background: rgba(255,255,255,.055);
  color: #fff; font: inherit; font-size: 26px; font-weight: 650;
  transition: border-color .2s, background .2s, box-shadow .2s;
}
input:focus { border-color: #ff4f8b; background: rgba(255,79,139,.10); box-shadow: 0 0 0 4px rgba(255,79,139,.16); }
.operator { padding-bottom: 15px; text-align: center; color: #ffb703; font-size: 26px; font-weight: 300; }
.actions { display: flex; gap: 9px; margin-top: 12px; }
button { border: 0; cursor: pointer; font: inherit; transition: transform .15s, opacity .2s, background .2s; }
button:active { transform: scale(.98); }
.primary { flex: 1; padding: 13px 16px; border-radius: 12px; background: #ff4f8b; color: #fff; font-weight: 750; }
.primary:hover { background: #ff729f; }
.ghost { padding: 13px 16px; border: 1px solid rgba(255,255,255,.1); border-radius: 12px; background: transparent; color: #a5a1b5; }
.ghost:hover { background: rgba(255,255,255,.05); }
.readout { margin-top: 16px; padding: 22px 20px; border-radius: 18px; background: linear-gradient(110deg, rgba(255,79,139,.24), rgba(255,183,3,.12)); }
.readout-label { color: #9f9bad; font-size: 10px; font-weight: 800; letter-spacing: 2px; }
.output-row { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-top: 5px; }
output { overflow-wrap: anywhere; font-size: clamp(42px, 10vw, 64px); font-weight: 680; letter-spacing: -3px; line-height: 1; }
.steps-badge { flex: 0 0 auto; margin-bottom: 5px; padding: 7px 10px; border: 1px solid rgba(255,255,255,.1); border-radius: 999px; color: #918c9d; font-size: 10px; }
.history { margin-top: 22px; }
.history-title { display: flex; justify-content: space-between; color: #7f7a8d; font-size: 10px; font-weight: 800; letter-spacing: 1.4px; }
.chips { display: flex; flex-wrap: wrap; gap: 7px; min-height: 28px; margin-top: 10px; }
.chip { padding: 7px 10px; border: 1px solid rgba(255,255,255,.08); border-radius: 8px; background: rgba(255,255,255,.035); color: #a9a4b5; font-size: 11px; }
.empty { color: #5f5b6d; font-size: 11px; }
</style>
</head>
<body>
<main class="shell">
  <section class="card">
    <div class="orb"></div>
    <div class="top">
      <div>
        <div class="badge"><i></i>VIBRANT FALLBACK</div>
        <h1>Sum<span>.</span>Studio</h1>
        <p class="caption">已切换为活泼艳丽风格的本地 Mock，鲜明配色和交互功能都保持在线。</p>
      </div>
      <div class="mark">＋</div>
    </div>
    <div class="calculator">
      <div class="inputs">
        <label class="field"><span>NUMBER A</span><input id="richA" type="number" step="any" value="128.5" aria-label="数字 A"></label>
        <div class="operator">＋</div>
        <label class="field"><span>NUMBER B</span><input id="richB" type="number" step="any" value="36" aria-label="数字 B"></label>
      </div>
      <div class="actions">
        <button class="primary" id="richAdd" type="button">记录这次计算</button>
        <button class="ghost" id="richReset" type="button">重置</button>
      </div>
    </div>
    <div class="readout">
      <div class="readout-label">LIVE RESULT</div>
      <div class="output-row">
        <output id="richResult">164.5</output>
        <span class="steps-badge">实时计算</span>
      </div>
    </div>
    <div class="history">
      <div class="history-title"><span>RECENT</span><span id="richCount">0 / 5</span></div>
      <div class="chips" id="richHistory"><span class="empty">点击「记录这次计算」保存结果</span></div>
    </div>
  </section>
</main>
<script>
(function () {
  var a = document.getElementById('richA');
  var b = document.getElementById('richB');
  var result = document.getElementById('richResult');
  var add = document.getElementById('richAdd');
  var reset = document.getElementById('richReset');
  var history = document.getElementById('richHistory');
  var count = document.getElementById('richCount');
  var entries = [];

  function formatNumber(value) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(10)));
  }

  function values() {
    var x = Number(a.value);
    var y = Number(b.value);
    return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
  }

  function render() {
    var pair = values();
    result.textContent = pair ? formatNumber(pair[0] + pair[1]) : '—';
  }

  function pulse() {
    if (!result.animate) return;
    result.animate(
      [{ transform: 'translateY(5px)', opacity: .45 }, { transform: 'translateY(0)', opacity: 1 }],
      { duration: 200, easing: 'cubic-bezier(.2,.8,.2,1)' }
    );
  }

  function drawHistory() {
    history.innerHTML = '';
    count.textContent = entries.length + ' / 5';
    if (!entries.length) {
      var empty = document.createElement('span');
      empty.className = 'empty';
      empty.textContent = '点击「记录这次计算」保存结果';
      history.appendChild(empty);
      return;
    }
    entries.forEach(function (entry) {
      var chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = entry;
      history.appendChild(chip);
    });
  }

  function addEntry() {
    var pair = values();
    if (!pair) {
      result.textContent = '请输入数字';
      return;
    }
    entries.unshift(formatNumber(pair[0]) + ' + ' + formatNumber(pair[1]) + ' = ' + formatNumber(pair[0] + pair[1]));
    entries = entries.slice(0, 5);
    drawHistory();
    pulse();
  }

  [a, b].forEach(function (input) {
    input.addEventListener('input', render);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') addEntry();
    });
  });
  add.addEventListener('click', addEntry);
  reset.addEventListener('click', function () {
    a.value = '0';
    b.value = '0';
    entries = [];
    render();
    drawHistory();
  });
  render();
})();
</script>
</body>
</html>
"""

# 普通模式不参与赛马风格，因此使用不带风格标签的中性 Mock 文案。
MOCK_HTML_NEUTRAL = (
    MOCK_HTML_SIMPLE
    .replace("RESTRAINED · FALLBACK", "MOCK · FALLBACK")
    .replace(
        "大模型暂不可用，已切换为庄重内敛风格的本地 Mock。功能依旧可以交互。",
        "大模型暂不可用，已自动切换为本地 Mock。功能依旧可以交互。",
    )
)


# ---------------------------------------------------------------------------
# 语言识别与 Mock 后端代码
# ---------------------------------------------------------------------------

LANGUAGE_LABELS = {
    "java": "Java",
    "python": "Python",
    "nodejs": "Node.js",
    "go": "Go",
    "php": "PHP",
    "csharp": "C#",
    "ruby": "Ruby",
    "rust": "Rust",
    "kotlin": "Kotlin",
}

_LANGUAGE_RULES = [
    ("kotlin", (r"\bkotlin\b",)),
    ("python", (r"\bpython\b", r"\bdjango\b", r"\bflask\b", r"\bfastapi\b")),
    ("nodejs", (r"\bnode(?:\.js|js)?\b", r"\bexpress\b", r"\bnestjs\b", r"后端.{0,6}js")),
    ("go", (r"\bgolang\b", r"\bgo\s*语言", r"后端.{0,4}go\b")),
    ("php", (r"\bphp\b", r"\blaravel\b", r"\bthinkphp\b")),
    ("csharp", (r"c#", r"\bcsharp\b", r"\.net\b", r"\basp\.net\b")),
    ("ruby", (r"\bruby\b", r"\brails\b")),
    ("rust", (r"\brust\b", r"\bactix\b", r"\baxum\b")),
    ("java", (r"\bjava\b", r"spring\s*boot", r"\bspring\b")),
]

_MOCK_BACKENDS = {
    "java": """// Mock 兜底后端 · Java 17 + Spring Boot
// 依赖：spring-boot-starter-web
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.*;

@SpringBootApplication
@RestController
@RequestMapping("/api")
@CrossOrigin
public class CalculatorApplication {

    public record AddRequest(double a, double b) {}
    public record AddResponse(double result) {}

    @PostMapping("/add")
    public AddResponse add(@RequestBody AddRequest request) {
        return new AddResponse(request.a() + request.b());
    }

    public static void main(String[] args) {
        SpringApplication.run(CalculatorApplication.class, args);
    }
}
""",
    "python": """# Mock 兜底后端 · Python + Flask
# 安装：pip install flask
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.post("/api/add")
def add():
    payload = request.get_json(silent=True) or {}
    try:
        result = float(payload["a"]) + float(payload["b"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="a 和 b 必须是数字"), 400
    return jsonify(result=result)


if __name__ == "__main__":
    app.run(port=8000)
""",
    "nodejs": """// Mock 兜底后端 · Node.js + Express
// 安装并运行：npm i express && node server.js
const express = require("express");
const app = express();
app.use(express.json());

app.post("/api/add", (req, res) => {
  const a = Number(req.body.a);
  const b = Number(req.body.b);
  if (!Number.isFinite(a) || !Number.isFinite(b)) {
    return res.status(400).json({ error: "a 和 b 必须是数字" });
  }
  res.json({ result: a + b });
});

app.listen(8000, () => console.log("http://localhost:8000"));
""",
    "go": """// Mock 兜底后端 · Go 1.22
package main

import (
    "encoding/json"
    "net/http"
)

type addRequest struct {
    A float64 `json:"a"`
    B float64 `json:"b"`
}

func main() {
    http.HandleFunc("/api/add", func(w http.ResponseWriter, r *http.Request) {
        var body addRequest
        if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
            http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
            return
        }
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(map[string]float64{"result": body.A + body.B})
    })
    http.ListenAndServe(":8000", nil)
}
""",
    "php": """<?php
// Mock 兜底后端 · PHP 8
header('Content-Type: application/json');
$payload = json_decode(file_get_contents('php://input'), true);
$a = filter_var($payload['a'] ?? null, FILTER_VALIDATE_FLOAT);
$b = filter_var($payload['b'] ?? null, FILTER_VALIDATE_FLOAT);
if ($a === false || $b === false) {
    http_response_code(400);
    echo json_encode(['error' => 'a 和 b 必须是数字']);
    exit;
}
echo json_encode(['result' => $a + $b]);
""",
    "csharp": """// Mock 兜底后端 · C# / ASP.NET Core Minimal API
var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.MapPost("/api/add", (AddRequest request) =>
    Results.Ok(new { result = request.A + request.B }));

app.Run();

public record AddRequest(double A, double B);
""",
    "ruby": """# Mock 兜底后端 · Ruby + Sinatra
# 安装：gem install sinatra
require "sinatra"
require "json"

post "/api/add" do
  body = JSON.parse(request.body.read)
  content_type :json
  { result: Float(body["a"]) + Float(body["b"]) }.to_json
rescue ArgumentError, KeyError
  status 400
  { error: "a 和 b 必须是数字" }.to_json
end
""",
    "rust": """// Mock 兜底后端 · Rust + Axum
use axum::{routing::post, Json, Router};
use serde::Deserialize;

#[derive(Deserialize)]
struct AddRequest {
    a: f64,
    b: f64,
}

#[tokio::main]
async fn main() {
    let app = Router::new().route("/api/add", post(add));
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8000").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn add(Json(body): Json<AddRequest>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "result": body.a + body.b }))
}
""",
    "kotlin": """// Mock 兜底后端 · Kotlin + Spring Boot
import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication
import org.springframework.web.bind.annotation.*

data class AddRequest(val a: Double = 0.0, val b: Double = 0.0)
data class AddResponse(val result: Double)

@SpringBootApplication
@RestController
@RequestMapping("/api")
class CalculatorApplication {
    @PostMapping("/add")
    fun add(@RequestBody request: AddRequest) = AddResponse(request.a + request.b)
}

fun main(args: Array<String>) = runApplication<CalculatorApplication>(*args)
""",
}


def detect_backend_language(prompt: str) -> str:
    text = prompt.lower()
    for language, patterns in _LANGUAGE_RULES:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return language
    return "java"


def language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language, language)


def _mock_backend(language: str) -> str:
    return _MOCK_BACKENDS.get(
        language,
        f"// Mock 兜底：当前环境未调用大模型，未生成 {language_label(language)} 后端代码。\n",
    )


# ---------------------------------------------------------------------------
# 大模型调用
# ---------------------------------------------------------------------------


def _call_llm(
    messages: list[dict],
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    api_key = settings.LLM_API_KEY
    if not api_key:
        raise LLMError("LLM_API_KEY 未配置")

    base_url = (settings.LLM_BASE_URL or "https://api.deepseek.com").rstrip("/")
    if base_url.endswith("/chat/completions"):
        endpoint = base_url
    else:
        endpoint = f"{base_url}/chat/completions"

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "model": settings.LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=LLM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise LLMError(f"模型请求失败：{exc}") from exc
    except ValueError as exc:
        raise LLMError("模型返回了无法解析的 JSON") from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型响应结构不符合 OpenAI 格式") from exc

    if not isinstance(content, str) or not content.strip():
        raise LLMError("模型返回内容为空")
    return content.strip()


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_+#.-]*\s*\n?(.*?)\n?```\s*$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _extract_html(content: str) -> str:
    text = _strip_markdown_fence(content)
    lower = text.lower()
    start = lower.find("<!doctype")
    if start < 0:
        start = lower.find("<html")
    if start > 0:
        text = text[start:]
        lower = text.lower()
    end = lower.rfind("</html>")
    if end >= 0:
        text = text[: end + len("</html>")]
    text = text.strip()
    if not text or ("<html" not in text.lower() and "<body" not in text.lower()):
        raise LLMError("模型未返回完整的 HTML 文档")
    return text


def _extract_code(content: str) -> str:
    text = _strip_markdown_fence(content)
    if not text:
        raise LLMError("模型未返回后端代码")
    return text


def _generate_html_with_fallback(prompt: str, mock_html: str) -> tuple[str, bool]:
    try:
        content = _call_llm(
            [
                {"role": "system", "content": FRONTEND_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.72,
            max_tokens=8000,
        )
        return _extract_html(content), False
    except Exception as exc:  # 网络、超时、格式异常都统一切 Mock
        logger.warning("HTML generation failed, falling back to mock: %s", exc)
        return mock_html, True


def _generate_backend_with_fallback(
    prompt: str, language: str
) -> tuple[str, bool]:
    label = language_label(language)
    backend_prompt = f"""请根据下面的产品需求生成配套后端代码。

产品需求：
{prompt}

后端要求：
- 使用 {label} 实现，可选择该语言主流的轻量框架。
- 提供与需求匹配的 API，至少包含一个可调用的示例接口和基本参数校验。
- 只输出一份完整、可直接运行的后端代码文件。
"""
    try:
        content = _call_llm(
            [
                {"role": "system", "content": BACKEND_SYSTEM_PROMPT},
                {"role": "user", "content": backend_prompt},
            ],
            temperature=0.25,
            max_tokens=6000,
        )
        return _extract_code(content), False
    except Exception as exc:
        logger.warning("Backend generation failed, falling back to mock: %s", exc)
        return _mock_backend(language), True


# ---------------------------------------------------------------------------
# 生成编排
# ---------------------------------------------------------------------------


def serialize_record(record: GenerateHistory) -> dict:
    return {
        "id": record.id,
        "prompt": record.prompt,
        "html_code": record.html_code,
        "backend_language": record.backend_language,
        "backend_language_label": language_label(record.backend_language),
        "backend_code": record.backend_code,
        "created_at": timezone.localtime(record.created_at).isoformat(),
    }


def _create_record(
    prompt: str, html_code: str, language: str, backend_code: str
) -> GenerateHistory:
    return GenerateHistory.objects.create(
        prompt=prompt,
        html_code=html_code,
        backend_language=language,
        backend_code=backend_code,
    )


def generate_single(prompt: str) -> dict:
    language = detect_backend_language(prompt)
    api_available = bool(settings.LLM_API_KEY)

    if not api_available:
        html_code = MOCK_HTML_NEUTRAL
        backend_code = _mock_backend(language)
        html_fallback = True
        backend_fallback = True
        fallback_reason = "未配置 LLM_API_KEY，已返回 Mock 加法计算器"
    else:
        # 普通模式不追加任何赛马风格指令，用户提示词原样交给模型。
        with ThreadPoolExecutor(max_workers=2) as executor:
            html_future = executor.submit(
                _generate_html_with_fallback, prompt, MOCK_HTML_NEUTRAL
            )
            backend_future = executor.submit(
                _generate_backend_with_fallback, prompt, language
            )
            html_code, html_fallback = html_future.result()
            backend_code, backend_fallback = backend_future.result()
        fallback_reason = (
            "大模型返回异常或超时，已返回 Mock 加法计算器"
            if html_fallback
            else ""
        )

    record = _create_record(prompt, html_code, language, backend_code)
    return {
        "mode": "single",
        "used_mock": html_fallback or backend_fallback,
        "html_fallback": html_fallback,
        "backend_fallback": backend_fallback,
        "fallback_reason": fallback_reason,
        "record": serialize_record(record),
    }


def generate_race(prompt: str) -> dict:
    language = detect_backend_language(prompt)
    simple_prompt = (
        f"{prompt}\n\n"
        "【本次生成风格】\n"
        "请采用庄重内敛的风格：整体正式、沉稳、有秩序感，适合商务或正式场景。"
        "必须使用有成体系的色彩，例如藏蓝、酒红、墨绿、深棕，搭配米白或象牙白，"
        "并用少量金色或铜色点缀；不要做成黑白、纯灰度或无彩色页面。"
        "排版要严谨，信息层级清晰，避免高饱和撞色、炫彩渐变和夸张动效。"
        "不得删减需求中的任何功能或交互。"
    )
    rich_prompt = (
        f"{prompt}\n\n"
        "【本次生成风格】\n"
        "请采用活泼艳丽的风格：使用鲜明、高饱和度的配色与明快的渐变，"
        "搭配圆润有活力的视觉元素和适度的动效，氛围轻松、有感染力，"
        "同时保证文字对比度和信息层级清晰，不得删减需求中的任何功能或交互。"
    )

    if not settings.LLM_API_KEY:
        simple_html, simple_fallback = MOCK_HTML_SIMPLE, True
        rich_html, rich_fallback = MOCK_HTML_RICH, True
        backend_code, backend_fallback = _mock_backend(language), True
        fallback_reason = "未配置 LLM_API_KEY，两个方案均使用 Mock 加法计算器"
    else:
        with ThreadPoolExecutor(max_workers=3) as executor:
            simple_future = executor.submit(
                _generate_html_with_fallback, simple_prompt, MOCK_HTML_SIMPLE
            )
            rich_future = executor.submit(
                _generate_html_with_fallback, rich_prompt, MOCK_HTML_RICH
            )
            backend_future = executor.submit(
                _generate_backend_with_fallback, prompt, language
            )
            simple_html, simple_fallback = simple_future.result()
            rich_html, rich_fallback = rich_future.result()
            backend_code, backend_fallback = backend_future.result()
        fallback_reason = (
            "至少一个大模型请求异常或超时，异常方案已使用 Mock 加法计算器"
            if simple_fallback or rich_fallback
            else ""
        )

    candidates = [
        {
            "variant": "simple",
            "label": "庄重内敛",
            "html_code": simple_html,
            "html_fallback": simple_fallback,
        },
        {
            "variant": "rich",
            "label": "活泼艳丽",
            "html_code": rich_html,
            "html_fallback": rich_fallback,
        },
    ]
    for candidate in candidates:
        candidate.update(
            {
                "prompt": prompt,
                "backend_language": language,
                "backend_language_label": language_label(language),
                "backend_code": backend_code,
            }
        )

    return {
        "mode": "race",
        "used_mock": simple_fallback or rich_fallback or backend_fallback,
        "backend_fallback": backend_fallback,
        "fallback_reason": fallback_reason,
        "results": candidates,
    }


def revise_html(html_code: str, suggestion: str, element: dict | None = None) -> tuple[str, bool, str]:
    """按建议修改 HTML。失败时原样返回，保证预览不中断。"""
    if not settings.LLM_API_KEY:
        return html_code, True, "未配置 LLM_API_KEY，Mock 模式下不支持在线修改，已保留原界面。"

    element_bits = []
    if isinstance(element, dict):
        tag = str(element.get("tag", ""))[:40]
        text = str(element.get("text", ""))[:160]
        selector = str(element.get("selector", ""))[:300]
        if tag:
            element_bits.append(f"标签：{tag}")
        if text:
            element_bits.append(f"元素文字：{text}")
        if selector:
            element_bits.append(f"选择器：{selector}")

    element_hint = "\n".join(element_bits) if element_bits else "用户未指定具体元素"
    user_prompt = f"""请基于现有 HTML 做定向修改，并返回修改后的完整单文件 HTML。

【用户修改建议】
{suggestion}

【用户点选的元素】
{element_hint}

【现有 HTML】
{html_code}

要求：保留未涉及区域的结构与可交互能力，输出完整 HTML，不要解释。"""

    try:
        content = _call_llm(
            [
                {"role": "system", "content": FRONTEND_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
        )
        return _extract_html(content), False, ""
    except Exception as exc:
        logger.warning("Revision failed, keeping original HTML: %s", exc)
        return html_code, True, "大模型修改失败或超时，已保留原界面，请稍后重试。"
