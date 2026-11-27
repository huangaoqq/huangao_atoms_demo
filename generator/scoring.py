"""Deterministic, explainable scoring for generated single-file web pages.

The score is intentionally based on inspectable HTML/CSS/JS signals instead of
another model's opinion, so both race candidates are judged by the same rules.
The rubric now covers requirement coverage, state handling, content quality and
performance in addition to structure, interaction, visual design, responsive
layout, accessibility and code safety.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser

MAX_SCORE = 100

DIMENSION_SPECS = (
    ("structure", "页面结构与完整性", 10),
    ("requirement", "需求覆盖", 15),
    ("interactivity", "交互能力", 15),
    ("state", "状态管理与持久化", 10),
    ("visual", "视觉设计", 10),
    ("responsive", "响应式适配", 10),
    ("accessibility", "可访问性", 10),
    ("content", "内容质量", 8),
    ("code_security", "代码与安全", 7),
    ("performance", "性能与资源", 5),
)

SEMANTIC_TAGS = {"header", "nav", "main", "footer", "section", "article", "aside"}
CONTROL_TAGS = {"input", "select", "textarea", "button"}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}

# Requirements are inferred from the same prompt for both candidates.  The
# checks are intentionally generic enough for generated pages but concrete
# enough to measure whether the requested app was actually implemented.
REQUIREMENT_RULES = (
    (
        r"2048|数字合并|方块合并",
        (
            ("4×4 棋盘", r"4\s*[x×*]\s*4|size\s*=\s*4|repeat\(\s*4\s*,"),
            ("方向键 / WASD / 触摸", r"arrow(?:up|down|left|right)|keydown|wasd|touch(?:start|move|end)|pointer(?:down|move|up)"),
            ("合并与实时计分", r"merge|合并|score|得分|计分"),
            ("最高分 / 本地记录", r"best|最高分|localstorage|sessionstorage"),
            ("重新开始 / 胜负状态", r"restart|new.?game|重新开始|再来一局|game.?over|游戏结束|won|胜利"),
        ),
    ),
    (
        r"计算器|calculator|四则运算|加减乘除",
        (
            ("数字与运算符输入", r"type=[\"']number|数字|运算符|<input|calculator"),
            ("四则运算逻辑", r"[+\-*/]|calculate|运算|eval|math"),
            ("计算结果展示", r"result|output|结果|显示"),
            ("计算历史", r"history|历史|记录"),
            ("清空 / 重置 / 键盘输入", r"clear|reset|清空|重置|keydown|enter"),
        ),
    ),
    (
        r"待办|todo|任务清单",
        (
            ("添加任务", r"add|添加|new.?task|<input"),
            ("优先级", r"priority|优先级|high|medium|low"),
            ("完成勾选 / 状态切换", r"checkbox|completed|完成|checked|toggle"),
            ("删除任务", r"delete|remove|删除"),
            ("筛选与剩余数量", r"filter|筛选|remaining|剩余|count|全部|进行中|已完成"),
        ),
    ),
    (
        r"扫雷|minesweeper",
        (
            ("难度选择", r"difficulty|初级|中级|高级|easy|medium|hard"),
            ("左键翻开 / 右键插旗", r"contextmenu|right.?click|flag|插旗|reveal|翻开"),
            ("雷数与计时器", r"mine|雷数|timer|计时"),
            ("首次点击不踩雷", r"first|首次|safe|安全"),
            ("胜负状态 / 重置", r"win|lose|游戏结束|胜利|失败|reset|restart|重置"),
        ),
    ),
    (
        r"作品集|portfolio|个人主页|个人作品",
        (
            ("个人介绍", r"about|关于|intro|介绍|bio"),
            ("技能标签", r"skill|技能|tag|标签"),
            ("项目案例", r"project|项目|case|作品"),
            ("联系方式", r"contact|联系|email|邮件|footer"),
            ("页内导航 / 动画 / 响应式", r"href=[\"']#|nav|导航|fade|animation|@media|grid|flex"),
        ),
    ),
    (
        r"番茄钟|pomodoro|专注计时",
        (
            ("计时器", r"timer|计时|countdown|setinterval|分钟"),
            ("开始 / 暂停", r"start|pause|开始|暂停"),
            ("重置", r"reset|重置|重启"),
            ("今日完成次数", r"count|次数|session|completed"),
            ("通知 / 持久化", r"notification|notify|提示|audio|localstorage|sessionstorage"),
        ),
    ),
)

PROMPT_STOPWORDS = {
    "支持", "可以", "需要", "使用", "显示", "界面", "应用", "一个", "实现",
    "功能", "进行", "所有", "以及", "包含", "完成", "提供", "并且", "同时",
    "能够", "用户", "点击", "页面", "默认", "简体", "中文", "适配", "移动",
    "设计", "现代", "简洁", "完整", "游戏", "项目", "需求", "生成", "增加",
    "修改", "修复", "设置", "通过", "这个", "每个", "如果", "但是", "因为",
    "所以", "开始", "结束", "数据", "状态", "内容", "the", "and", "with",
    "for", "from", "this", "that", "into", "your", "have", "will", "should",
}


class _PageAnalyzer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.end_tags: Counter[str] = Counter()
        self.attributes: list[tuple[str, dict[str, str]]] = []
        self.text_chunks: list[str] = []
        self.style_chunks: list[str] = []
        self.script_chunks: list[str] = []
        self.ids: list[str] = []
        self.event_attribute_chunks: list[str] = []
        self.event_attribute_names: list[str] = []
        self.event_attribute_count = 0
        self.aria_count = 0
        self.image_count = 0
        self.image_alt_count = 0
        self.control_count = 0
        self._raw_tag: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        self.tags[tag] += 1
        self.attributes.append((tag, attr_map))

        if tag in {"script", "style"}:
            self._raw_tag = tag
        if tag == "img":
            self.image_count += 1
            if attr_map.get("alt", "").strip():
                self.image_alt_count += 1
        if tag in CONTROL_TAGS:
            input_type = attr_map.get("type", "text").lower()
            if tag != "input" or input_type != "hidden":
                self.control_count += 1
        if "id" in attr_map and attr_map["id"].strip():
            self.ids.append(attr_map["id"].strip())

        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                self.event_attribute_count += 1
                self.event_attribute_names.append(name[2:])
                self.event_attribute_chunks.append(value or "")
            if name.startswith("aria-"):
                self.aria_count += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self.end_tags[tag] += 1
        if tag == self._raw_tag:
            self._raw_tag = None

    def handle_data(self, data: str) -> None:
        if self._raw_tag == "style":
            self.style_chunks.append(data)
        elif self._raw_tag == "script":
            self.script_chunks.append(data)
        else:
            self.text_chunks.append(data)


def _check(label: str, earned: float, maximum: float) -> dict:
    return {
        "label": label,
        "earned": round(float(earned), 1),
        "max": round(float(maximum), 1),
    }


def _dimension(key: str, label: str, checks: list[dict]) -> dict:
    maximum = sum(item["max"] for item in checks)
    return {
        "key": key,
        "label": label,
        "score": round(sum(item["earned"] for item in checks), 1),
        "max": round(maximum, 1),
        "checks": checks,
    }


def _empty_result(reason: str = "Mock 兜底，不参与评分") -> dict:
    return {
        "total": 0.0,
        "max": MAX_SCORE,
        "grade": "Mock 兜底",
        "fallback": True,
        "summary": reason,
        "dimensions": [
            {
                "key": key,
                "label": label,
                "score": 0.0,
                "max": maximum,
                "checks": [_check(reason, 0, maximum)],
            }
            for key, label, maximum in DIMENSION_SPECS
        ],
        "tie_breaker": 0,
    }


def _grade(total: float) -> str:
    if total >= 90:
        return "优秀"
    if total >= 80:
        return "良好"
    if total >= 70:
        return "合格"
    if total >= 60:
        return "待改进"
    return "需重做"


def _has(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _prompt_keyword_hit(prompt: str, haystack: str) -> bool:
    if not prompt:
        return False
    candidates = []
    candidates.extend(
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", prompt)
        if token.lower() not in PROMPT_STOPWORDS
    )
    for block in re.findall(r"[一-鿿]{2,12}", prompt):
        for size in (4, 3, 2):
            for index in range(0, len(block) - size + 1):
                token = block[index:index + size]
                if token not in PROMPT_STOPWORDS:
                    candidates.append(token)
    seen = set()
    for token in candidates:
        if token in seen:
            continue
        seen.add(token)
        if token in haystack:
            return True
        if len(seen) >= 40:
            break
    return False


def _requirement_checks(prompt: str, lower: str, visible_text: str, analyzer: _PageAnalyzer,
                        js_lower: str) -> list[dict]:
    prompt = prompt or ""
    features = None
    for pattern, candidates in REQUIREMENT_RULES:
        if re.search(pattern, prompt, flags=re.IGNORECASE):
            features = list(candidates[:5])
            break
    if features:
        per = 15 / len(features)
        return [
            _check(f"需求：{label}", per if _has(pattern, lower) else 0, per)
            for label, pattern in features
        ]

    haystack = lower + "\n" + visible_text.lower()
    has_controls = analyzer.control_count > 0 or analyzer.tags["button"] > 0
    has_logic = bool(re.search(r"\bfunction\b|=>|\bif\s*\(|\bfor\s*\(|\.map\(|\.foreach\(", js_lower))
    has_content = len(visible_text) >= 80
    has_state = _has(
        r"localstorage|sessionstorage|reset|restart|clear|重置|重新开始|state|render",
        lower,
    )
    keyword_hit = _prompt_keyword_hit(prompt, haystack)
    return [
        _check("核心操作控件", 3 if has_controls else 0, 3),
        _check("交互逻辑", 3 if has_logic else 0, 3),
        _check("内容/结果展示", 3 if has_content else 0, 3),
        _check("状态更新或重置", 3 if has_state else 0, 3),
        _check("需求关键词呼应", 3 if keyword_hit else 0, 3),
    ]


def score_html(html_code: str, is_mock: bool = False, prompt: str = "") -> dict:
    """Score a generated page across ten weighted dimensions (100 points)."""
    if is_mock or not html_code:
        return _empty_result()

    try:
        analyzer = _PageAnalyzer()
        analyzer.feed(html_code)
        analyzer.close()
    except Exception:
        result = _empty_result("HTML 解析异常，评分按 0 分处理")
        result["grade"] = "需重做"
        return result

    lower = html_code.lower()
    css = "\n".join(analyzer.style_chunks + [
        attrs.get("style", "") for _, attrs in analyzer.attributes
    ])
    js = "\n".join(analyzer.script_chunks + analyzer.event_attribute_chunks)
    visible_text = re.sub(r"\s+", " ", " ".join(analyzer.text_chunks)).strip()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_code, flags=re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    css_lower = css.lower()
    js_lower = js.lower()
    visible_lower = visible_text.lower()

    has_doctype = "<!doctype html" in lower
    has_html_close = "</html>" in lower
    has_viewport = bool(re.search(
        r'<meta[^>]+name=["\']viewport["\'][^>]*content=["\'][^"\']+["\']',
        html_code,
        flags=re.IGNORECASE,
    ))
    has_stylesheet = bool(analyzer.style_chunks) or any(
        tag == "link" and "stylesheet" in attrs.get("rel", "").lower()
        for tag, attrs in analyzer.attributes
    )
    style_text = css.strip()
    heading_count = sum(analyzer.tags[tag] for tag in ("h1", "h2", "h3", "h4", "h5", "h6"))

    css_properties = set(re.findall(r"(?:^|[;{])\s*([a-zA-Z-]{2,})\s*:", css))
    effect_tokens = (
        "border-radius", "box-shadow", "gradient(", "transition", "animation",
        "transform", "backdrop-filter", "filter",
    )
    effect_count = sum(1 for token in effect_tokens if token in css_lower)
    has_flex = bool(re.search(r"display\s*:\s*(?:inline-)?flex", css_lower))
    has_grid = bool(re.search(r"display\s*:\s*(?:inline-)?grid", css_lower))
    media_count = len(re.findall(r"@media\b", css_lower))
    has_css_variables = bool(re.search(r"--[a-z0-9-]+\s*:", css_lower))
    has_font_declaration = "font-family" in css_lower
    relative_unit_count = len(re.findall(
        r"\b\d*\.?\d+(?:%|rem|em|vh|vw|vmin|vmax)\b|clamp\(", css_lower
    ))
    has_fluid_sizing = bool(re.search(
        r"max-width\s*:|width\s*:\s*100%|min\(|clamp\(|aspect-ratio", css_lower
    ))
    has_color_styling = "color:" in css_lower and (
        "background" in css_lower or "border" in css_lower
    )
    has_spacing = bool(re.search(r"\b(?:gap|padding|margin|border-radius)\s*:", css_lower))

    event_bindings = js_lower.count("addeventlistener") + analyzer.event_attribute_count
    event_types = set(
        re.findall(r"addeventlistener\(\s*[\"']([a-z]+)", js_lower)
    )
    event_types.update(analyzer.event_attribute_names)
    event_types.update(re.findall(r"\.on([a-z]+)\s*=", js_lower))
    event_type_count = len(event_types)
    dom_feedback = len(set(re.findall(
        r"textcontent|innerhtml|innertext|classlist|setattribute|appendchild|"
        r"\.style\.|\.value\s*=|queryselector",
        js_lower,
    )))
    js_logic = sum(bool(re.search(pattern, js_lower)) for pattern in (
        r"\bfunction\b|=>", r"\bif\s*\(", r"\bfor\s*\(|\.foreach\(|\.map\(",
        r"\bwhile\s*\(|\bswitch\s*\(", r"try\s*\{",
    ))
    has_focus_style = bool(re.search(r":focus(?:-visible|-within)?\b", css_lower))
    has_touch = _has(r"touchstart|touchmove|touchend|pointerdown|pointermove|pointerup|swipe", lower)
    has_overflow_adaptation = _has(
        r"overflow-x|max-width\s*:\s*100%|object-fit|aspect-ratio|flex-wrap|repeat\(auto",
        css_lower,
    )

    persistence = _has(r"localstorage|sessionstorage|indexeddb", lower)
    state_signals = _has(r"\bstate\b|\brender\b|\bupdate\b|setstate|usestate|reactive|data-", lower) or dom_feedback >= 2
    reset_signals = _has(r"reset|restart|new.?game|clear|重置|重新开始|清空|再来一局", lower)
    history_signals = _has(r"history|undo|redo|历史|记录|previous|moves|步数", lower)
    validation_signals = _has(
        r"try\s*\{|catch\s*\(|validate|validation|invalid|error|isnan|parseint|"
        r"required|pattern\s*=|maxlength|min\s*=|max\s*=",
        lower,
    )

    duplicate_ids = sum(count - 1 for count in Counter(analyzer.ids).values() if count > 1)
    insecure_http = bool(re.search(
        r"(?:src|href)\s*=\s*[\"']http://|url\(\s*[\"']?http://",
        lower,
    ))
    dangerous_js = bool(re.search(r"\beval\s*\(|document\.write\s*\(|new\s+function\s*\(", js_lower))
    placeholders = bool(re.search(r"(?:\btodo\b|\bfixme\b)\s*:|lorem ipsum", lower))
    code_size = len(html_code)
    semantic_count = sum(analyzer.tags[tag] for tag in SEMANTIC_TAGS)
    text_density = len(visible_text) >= 200

    if analyzer.control_count == 0:
        label_score = 1
    elif analyzer.tags["label"] or analyzer.aria_count:
        label_score = 1
    else:
        label_score = 0

    if analyzer.image_count == 0:
        alt_score = 1
    elif analyzer.image_alt_count == analyzer.image_count:
        alt_score = 1
    elif analyzer.image_alt_count:
        alt_score = 0
    else:
        alt_score = 0

    button_count = analyzer.tags["button"]
    accessible_buttons = len(re.findall(
        r"<button[^>]*(?:aria-label|title)\s*=", html_code, flags=re.IGNORECASE
    )) + len(re.findall(r"<button[^>]*>\s*[^<\s]", html_code, flags=re.IGNORECASE))
    if button_count == 0:
        button_name_score = 1
    elif accessible_buttons >= max(1, button_count // 2):
        button_name_score = 1
    else:
        button_name_score = 0

    has_aria_live = bool(re.search(r"aria-live\s*=|role\s*=", lower))
    open_nonvoid = sum(
        count for tag, count in analyzer.tags.items() if tag not in VOID_TAGS
    )
    closed_nonvoid = sum(
        count for tag, count in analyzer.end_tags.items() if tag not in VOID_TAGS
    )
    closure_ratio = closed_nonvoid / open_nonvoid if open_nonvoid else 1

    external_urls = re.findall(
        r"<(?:script|link|img|source)[^>]+(?:src|href)\s*=\s*[\"']([^\"']+)[\"']",
        html_code,
        flags=re.IGNORECASE,
    )
    external_count = sum(
        1 for url in external_urls
        if url.startswith(("http://", "https://", "//"))
    )
    dom_nodes = sum(analyzer.tags.values())
    inline_asset_size = len(css) + len(js)
    huge_data_uri = bool(re.search(r"data:[^;]+;base64,[A-Za-z0-9+/=]{40000,}", html_code))

    structure = _dimension("structure", "页面结构与完整性", [
        _check("DOCTYPE 声明", 1 if has_doctype else 0, 1),
        _check("HTML / HEAD / BODY", (
            2 if analyzer.tags["html"] and analyzer.tags["head"] and analyzer.tags["body"]
            else 1 if analyzer.tags["html"] or analyzer.tags["body"] else 0
        ), 2),
        _check("页面标题", 1 if title else 0, 1),
        _check("移动端 viewport", 1 if has_viewport else 0, 1),
        _check("CSS 样式资源", 1 if len(style_text) >= 80 or has_stylesheet else 0, 1),
        _check("可见正文内容", 1 if len(visible_text) >= 80 else 0, 1),
        _check("语义化结构标签", 1 if semantic_count else 0, 1),
        _check("HTML 文档完整结束", 1 if has_html_close else 0, 1),
        _check("标题层级", 1 if heading_count else 0, 1),
    ])

    requirement = _dimension(
        "requirement",
        "需求覆盖",
        _requirement_checks(prompt, lower, visible_text, analyzer, js_lower),
    )

    interactivity = _dimension("interactivity", "交互能力", [
        _check("JavaScript 脚本", 2 if analyzer.tags["script"] else 0, 2),
        _check("可操作控件", 3 if analyzer.control_count >= 3 else (1 if analyzer.control_count else 0), 3),
        _check("事件绑定", 3 if event_bindings >= 3 else (2 if event_bindings else 0), 3),
        _check("事件类型多样性", 3 if event_type_count >= 3 else (1 if event_type_count else 0), 3),
        _check("DOM / 状态反馈", 2 if dom_feedback >= 2 else (1 if dom_feedback else 0), 2),
        _check("业务逻辑复杂度", 2 if js_logic >= 2 else (1 if js_logic else 0), 2),
    ])

    state = _dimension("state", "状态管理与持久化", [
        _check("本地持久化", 2 if persistence else 0, 2),
        _check("状态更新 / 渲染", 2 if state_signals else 0, 2),
        _check("重置 / 重新开始", 2 if reset_signals else 0, 2),
        _check("历史 / 记录 / 撤销", 2 if history_signals else 0, 2),
        _check("输入校验 / 错误处理", 2 if validation_signals else 0, 2),
    ])

    property_points = 2 if len(css_properties) >= 12 else (1 if len(css_properties) >= 6 else 0)
    effect_points = 2 if effect_count >= 3 else (1 if effect_count else 0)
    design_points = (1 if has_css_variables else 0) + (1 if has_font_declaration else 0)
    visual = _dimension("visual", "视觉设计", [
        _check("CSS 规则丰富度", property_points, 2),
        _check("Flex / Grid 布局", (1 if has_flex else 0) + (1 if has_grid else 0), 2),
        _check("阴影 / 圆角 / 渐变 / 动效", effect_points, 2),
        _check("设计变量与字体", design_points, 2),
        _check("颜色与背景体系", 1 if has_color_styling else 0, 1),
        _check("间距与布局约束", 1 if has_spacing else 0, 1),
    ])

    relative_points = 2 if relative_unit_count >= 3 else (1 if relative_unit_count else 0)
    responsive = _dimension("responsive", "响应式适配", [
        _check("viewport 元信息", 1 if has_viewport else 0, 1),
        _check("媒体查询", 3 if media_count >= 2 else (2 if media_count else 0), 3),
        _check("相对单位", relative_points, 2),
        _check("流式尺寸约束", 2 if has_fluid_sizing else 0, 2),
        _check("触摸 / 指针交互", 1 if has_touch else 0, 1),
        _check("溢出与自适应内容", 1 if has_overflow_adaptation else 0, 1),
    ])

    semantic_points = 1 if semantic_count else 0
    readable_points = 2 if title and len(visible_text) >= 40 else (1 if title or visible_text else 0)
    accessibility = _dimension("accessibility", "可访问性", [
        _check("页面语言 lang", 1 if analyzer.tags["html"] and re.search(r"<html[^>]+lang=", lower) else 0, 1),
        _check("语义化结构标签", semantic_points, 1),
        _check("图片替代文本", alt_score, 1),
        _check("表单标签", label_score, 1),
        _check("ARIA 属性", 1 if analyzer.aria_count else 0, 1),
        _check("键盘焦点样式", 1 if has_focus_style else 0, 1),
        _check("按钮可访问名称", button_name_score, 1),
        _check("ARIA live / role", 1 if has_aria_live else 0, 1),
        _check("标题与可读正文", readable_points, 2),
    ])

    content_variety = sum(
        1 for tag in ("ul", "ol", "table", "section", "article", "form")
        if analyzer.tags[tag]
    )
    content = _dimension("content", "内容质量", [
        _check("可见正文丰富度", 2 if len(visible_text) >= 80 else (1 if visible_text else 0), 2),
        _check("标题层级", 1 if heading_count else 0, 1),
        _check("内容区块多样性", 2 if content_variety >= 3 else (1 if content_variety else 0), 2),
        _check("无 TODO / 占位文案", 0 if placeholders else 1, 1),
        _check("说明 / 引导文案", 1 if _has(r"placeholder|aria-label|<label|提示|说明|帮助|instruction|help", lower) else 0, 1),
        _check("文本信息密度", 1 if text_density else 0, 1),
    ])

    code_security = _dimension("code_security", "代码与安全", [
        _check("无危险执行 API", 0 if dangerous_js else 1, 1),
        _check("ID 唯一性", 0 if duplicate_ids else 1, 1),
        _check("无 HTTP 明文资源", 0 if insecure_http else 1, 1),
        _check("无 TODO 占位内容", 0 if placeholders else 1, 1),
        _check("文件体积合理", 1 if 1000 <= code_size <= 400000 else 0, 1),
        _check("结构化 JavaScript", 1 if re.search(r"\bfunction\b|=>|\bclass\b", js_lower) else 0, 1),
        _check("标签闭合完整性", 1 if closure_ratio >= 0.85 else 0, 1),
    ])

    performance = _dimension("performance", "性能与资源", [
        _check("外部资源数量", 2 if external_count <= 2 else (1 if external_count <= 4 else 0), 2),
        _check("DOM 节点规模", 1 if dom_nodes <= 1200 else 0, 1),
        _check("内联资源体积", 1 if inline_asset_size <= 300000 else 0, 1),
        _check("无超大 Base64 资源", 0 if huge_data_uri else 1, 1),
    ])

    dimensions = [
        structure,
        requirement,
        interactivity,
        state,
        visual,
        responsive,
        accessibility,
        content,
        code_security,
        performance,
    ]
    total = round(sum(item["score"] for item in dimensions), 1)
    weakest = min(dimensions, key=lambda item: item["score"] / max(item["max"], 1))
    strong_dimensions = [
        item["label"] for item in dimensions if item["score"] / max(item["max"], 1) >= 0.8
    ]
    summary = (
        f"{'、'.join(strong_dimensions)}表现较好"
        if strong_dimensions else "页面基础能力已具备"
    )
    if weakest["score"] / max(weakest["max"], 1) < 0.7:
        summary += f"；{weakest['label']}仍有提升空间"

    tie_breaker = round(
        requirement["score"] * 1.5
        + interactivity["score"]
        + state["score"]
        + content["score"] * 0.5,
        2,
    )
    return {
        "total": total,
        "max": MAX_SCORE,
        "grade": _grade(total),
        "fallback": False,
        "summary": summary,
        "dimensions": dimensions,
        "tie_breaker": tie_breaker,
    }
