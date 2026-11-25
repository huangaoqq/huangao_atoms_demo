import io
import json
import logging
import zipfile

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import GenerateHistory

logger = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 2000
MAX_SUGGESTION_LENGTH = 1000
MAX_CODE_LENGTH = 2_000_000

BACKEND_FILE_EXTENSIONS = {
    "java": "java",
    "python": "py",
    "nodejs": "js",
    "go": "go",
    "php": "php",
    "csharp": "cs",
    "ruby": "rb",
    "rust": "rs",
    "kotlin": "kt",
}


def index(request):
    records = GenerateHistory.objects.all()
    history = [services.serialize_record(record) for record in records]
    context = {
        "history": history,
        "llm_enabled": bool(settings.LLM_API_KEY),
        "default_backend": "Java",
    }
    return render(request, "index.html", context)


def _json_error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _parse_payload(request):
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@require_POST
def api_generate(request):
    payload = _parse_payload(request)
    if payload is None:
        return _json_error("请求体必须是 JSON 对象")

    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return _json_error("请输入页面需求")
    if len(prompt) > MAX_PROMPT_LENGTH:
        return _json_error(f"需求描述不能超过 {MAX_PROMPT_LENGTH} 个字符")

    race_mode = bool(payload.get("race_mode"))

    try:
        result = services.generate_race(prompt) if race_mode else services.generate_single(prompt)
    except Exception:
        logger.exception("Generation endpoint failed unexpectedly")
        return _json_error("生成失败，请稍后重试", status=500)

    return JsonResponse({"ok": True, **result})


@require_POST
def api_select(request):
    payload = _parse_payload(request)
    if payload is None:
        return _json_error("请求体必须是 JSON 对象")

    prompt = str(payload.get("prompt", "")).strip()
    html_code = str(payload.get("html_code", ""))
    backend_code = str(payload.get("backend_code", ""))
    language = str(payload.get("backend_language", "")).strip()

    if not prompt:
        return _json_error("缺少原始需求")
    if not html_code.strip():
        return _json_error("缺少 HTML 代码")
    if len(html_code) > MAX_CODE_LENGTH or len(backend_code) > MAX_CODE_LENGTH:
        return _json_error("生成内容过大")
    if not language:
        language = services.detect_backend_language(prompt)
    language = language[:32]

    record = GenerateHistory.objects.create(
        prompt=prompt,
        html_code=html_code,
        backend_language=language,
        backend_code=backend_code,
    )
    return JsonResponse({"ok": True, "record": services.serialize_record(record)})


@require_POST
def api_revise(request):
    payload = _parse_payload(request)
    if payload is None:
        return _json_error("请求体必须是 JSON 对象")

    html_code = str(payload.get("html_code", ""))
    suggestion = str(payload.get("suggestion", "")).strip()
    element = payload.get("element") if isinstance(payload.get("element"), dict) else {}

    if not html_code.strip():
        return _json_error("当前界面代码为空")
    if len(html_code) > MAX_CODE_LENGTH:
        return _json_error("界面代码过大")
    if not suggestion:
        return _json_error("请输入修改建议")
    if len(suggestion) > MAX_SUGGESTION_LENGTH:
        return _json_error(f"修改建议不能超过 {MAX_SUGGESTION_LENGTH} 个字符")

    record = None
    history_id = payload.get("history_id")
    if history_id not in (None, ""):
        try:
            record = GenerateHistory.objects.filter(pk=int(history_id)).first()
        except (TypeError, ValueError):
            record = None

    revised_html, used_mock, message = services.revise_html(
        html_code=html_code,
        suggestion=suggestion,
        element=element,
    )

    record_payload = None
    if not used_mock:
        if record is not None:
            record.html_code = revised_html
            record.save(update_fields=["html_code"])
        record_payload = services.serialize_record(record) if record else None

    return JsonResponse(
        {
            "ok": True,
            "html_code": revised_html,
            "used_mock": used_mock,
            "message": message,
            "record": record_payload,
        }
    )


@require_POST
def api_delete_history(request, history_id):
    record = GenerateHistory.objects.filter(pk=history_id).first()
    if record is None:
        return _json_error("生成记录不存在或已删除", status=404)
    record.delete()
    return JsonResponse({"ok": True, "deleted_id": history_id})


@require_GET
def api_download_history(request, history_id):
    record = GenerateHistory.objects.filter(pk=history_id).first()
    if record is None:
        return _json_error("生成记录不存在或已删除", status=404)

    extension = BACKEND_FILE_EXTENSIONS.get(record.backend_language, "txt")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", record.html_code)
        if record.backend_code.strip():
            archive.writestr(f"backend.{extension}", record.backend_code)

    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="loom-history-{record.pk}.zip"'
    )
    return response
