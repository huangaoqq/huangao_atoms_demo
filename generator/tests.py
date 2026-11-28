import base64
import json

from django.test import TestCase

from .models import (
    GenerateHistory,
    GenerateVersion,
    GenerationJob,
    PublishedPage,
)
from .scoring import score_html
from .views import (
    _frontend_project_is_complete,
    _mock_frontend_project,
    _normalize_image_meta,
    _normalize_reference_image,
    _parse_project_bundle,
    _project_is_complete,
    _wants_frontend_only,
)


class ScoringTests(TestCase):
    def test_score_has_ten_dimensions_and_prompt_coverage(self):
        html = """<!doctype html>
<html lang="zh-CN"><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2048</title>
<style>
:root { --cell: #fff; }
body { padding: 20px; font-family: sans-serif; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
@media (max-width: 600px) { body { padding: 8px; } }
</style></head>
<body><main><h1>2048 · 4x4 棋盘</h1>
<p>方向键 / WASD / 触摸滑动，合并相同数字并实时计分。</p>
<div>得分 <span id="score">0</span> 最高分 <span id="best">0</span></div>
<div class="grid" id="grid"></div>
<button id="restart">重新开始</button>
<script>
const SIZE = 4;
let score = 0, best = Number(localStorage.getItem('best') || 0);
function render() { document.getElementById('score').textContent = score; }
function move() {
  score += 2;
  if (score > best) { best = score; localStorage.setItem('best', best); }
  render();
}
document.getElementById('restart').addEventListener('click', () => { score = 0; render(); });
document.addEventListener('keydown', (event) => {
  if (event.key.startsWith('Arrow')) move();
});
</script></main></body></html>"""
        result = score_html(
            html,
            prompt="请生成一个完整可玩的 2048 游戏：4x4 棋盘，支持方向键、WASD 和触摸滑动，合并相同数字并实时计分，显示最高分和重新开始。",
        )
        self.assertEqual(len(result["dimensions"]), 10)
        self.assertEqual(sum(item["max"] for item in result["dimensions"]), 100)
        self.assertLessEqual(result["total"], 100)
        requirement = next(
            item for item in result["dimensions"] if item["key"] == "requirement"
        )
        self.assertGreater(requirement["score"], 0)

    def test_mock_score_is_zero(self):
        result = score_html("<html></html>", is_mock=True)
        self.assertEqual(result["total"], 0)
        self.assertTrue(result["fallback"])


class ProjectBundleParserTests(TestCase):
    def test_accepts_missing_end_file_markers(self):
        raw = """===FILES===
===FILE: requirements.txt===
Django==4.2
===FILE: manage.py===
print("ok")
===FILE: config/settings.py===
SECRET_KEY = "x"
===FILE: apps/core/views.py===
def index(request): pass
===FILE: templates/index.html===
<!doctype html><html><body><h1>App</h1></body></html>
===FILE: static/app.css===
body { padding: 20px; }
===FILE: static/app.js===
console.log("ok")
===END_FILE===
===PREVIEW_HTML===
<!doctype html><html><body><h1>App preview</h1></body></html>
===END_PREVIEW==="""
        parsed = _parse_project_bundle(raw, "Django")
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["files"]), 8)
        self.assertTrue(_project_is_complete(parsed))
        self.assertTrue(
            any(item["path"] == "templates/index.html" for item in parsed["files"])
        )

    def test_rejects_two_file_bundle(self):
        parsed = {
            "files": [
                {"path": "requirements.txt", "content": "Django==4.2"},
                {"path": "DEPLOY.md", "content": "deploy"},
            ],
            "preview_html": "<html></html>",
        }
        self.assertFalse(_project_is_complete(parsed))


class JobEndpointTests(TestCase):
    def test_delete_active_job_removes_task(self):
        job = GenerationJob.objects.create(
            client_id="test-client",
            prompt="test",
            requested_mode="single",
            mode="single",
            status=GenerationJob.STATUS_RUNNING,
            progress=20,
            state={"stages": []},
            project_type="backend",
            backend_label="Django",
        )
        response = self.client.post(
            f"/api/jobs/{job.id}/delete/", HTTP_HOST="localhost"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GenerationJob.objects.filter(pk=job.pk).exists())

    def test_start_rejects_unknown_provider(self):
        response = self.client.post(
            "/api/jobs/start/",
            data=json.dumps(
                {
                    "prompt": "test",
                    "mode": "single",
                    "provider": "unknown",
                    "client_id": "test-client",
                }
            ),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 400)


class RaceStatsTests(TestCase):
    def _race_job(self, winner: str, deepseek_score: float, qwen_score: float):
        models = [
            {
                "provider": "deepseek",
                "label": "DeepSeek",
                "model": "deepseek-chat",
                "is_mock": False,
                "score": {"total": deepseek_score, "dimensions": []},
            },
            {
                "provider": "qwen",
                "label": "千问",
                "model": "qwen3.6-flash",
                "is_mock": False,
                "score": {"total": qwen_score, "dimensions": []},
            },
        ]
        return GenerationJob.objects.create(
            client_id="stats-client",
            prompt="stats test",
            requested_mode="race",
            mode="race",
            status=GenerationJob.STATUS_COMPLETED,
            progress=100,
            state={
                "race": {
                    "models": models,
                    "winner": next(
                        model for model in models if model["provider"] == winner
                    ),
                }
            },
            project_type="backend",
        )

    def test_stats_count_both_winners_and_use_all_race_tasks(self):
        self._race_job("deepseek", 91, 80)
        self._race_job("qwen", 78, 88)
        response = self.client.get("/api/race/stats/", HTTP_HOST="localhost")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_races"], 2)
        self.assertEqual(
            {item["provider"]: item["wins"] for item in data["models"]},
            {"deepseek": 1, "qwen": 1},
        )
        self.assertIsNone(data["recommended"])
        self.assertEqual(len(data["races"]), 2)


class FrontendProjectTests(TestCase):
    def test_explicit_frontend_only_phrases(self):
        self.assertTrue(_wants_frontend_only("只生成前端代码，不要后端"))
        self.assertTrue(_wants_frontend_only("纯前端页面，用 React"))
        self.assertFalse(_wants_frontend_only("生成一个完整的 Django 网站"))

    def test_mock_frontend_project_is_complete(self):
        project = _mock_frontend_project()
        self.assertTrue(_frontend_project_is_complete(project))
        paths = [item["path"] for item in project["files"]]
        self.assertIn("index.html", paths)
        self.assertIn("styles/app.css", paths)
        self.assertIn("DEPLOY.md", paths)
        self.assertFalse(any(path.endswith("manage.py") for path in paths))

    def test_frontend_validation_rejects_backend_bundle(self):
        project = {
            "files": [
                {"path": "manage.py", "content": "x"},
                {"path": "requirements.txt", "content": "Django"},
                {"path": "templates/index.html", "content": "x"},
                {"path": "static/app.js", "content": "x"},
                {"path": "README.md", "content": "x"},
                {"path": "DEPLOY.md", "content": "x"},
            ],
            "preview_html": "<html></html>",
        }
        self.assertFalse(_frontend_project_is_complete(project))


class ReferenceImageTests(TestCase):
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII="
    )

    def test_valid_image_data_url_is_normalized(self):
        raw = "data:image/png;base64," + base64.b64encode(self.PNG).decode()
        normalized = _normalize_reference_image(raw)
        self.assertTrue(normalized.startswith("data:image/png;base64,"))

    def test_invalid_image_is_rejected(self):
        with self.assertRaises(ValueError):
            _normalize_reference_image("not-an-image")

    def test_image_meta_is_sanitized(self):
        meta = _normalize_image_meta(
            {
                "width": 800,
                "height": 600,
                "brightness": 0.42,
                "palette": ["#112233", "invalid"],
                "name": "reference.png",
                "ignored": "x",
            }
        )
        self.assertEqual(meta["width"], 800)
        self.assertEqual(meta["height"], 600)
        self.assertEqual(meta["palette"], ["#112233"])
        self.assertEqual(meta["name"], "reference.png")


class DeleteHistoryTests(TestCase):
    def test_delete_project_cascades_versions_and_publication(self):
        history = GenerateHistory.objects.create(
            prompt="delete project",
            html_code="<html></html>",
            project_type="backend",
        )
        version = GenerateVersion.objects.create(
            history=history,
            version_no=1,
            html_code="<html></html>",
            files=[],
        )
        publication = PublishedPage.objects.create(
            version=version, slug="delete-history-test"
        )
        response = self.client.post(
            f"/api/history/{history.id}/delete/", HTTP_HOST="localhost"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["versions_deleted"], 1)
        self.assertEqual(data["published_deleted"], 1)
        self.assertFalse(GenerateHistory.objects.filter(pk=history.pk).exists())
        self.assertFalse(GenerateVersion.objects.filter(pk=version.pk).exists())
        self.assertFalse(PublishedPage.objects.filter(pk=publication.pk).exists())


class SourceEditTests(TestCase):
    def _project(self):
        history = GenerateHistory.objects.create(
            prompt="source edit test",
            html_code="<html><body><h1>old</h1></body></html>",
            project_type="frontend",
            backend_label="React",
        )
        version = GenerateVersion.objects.create(
            history=history,
            version_no=1,
            html_code="<html><body><h1>old</h1></body></html>",
            files=[
                {"path": "index.html", "content": "<html><body>old</body></html>"},
                {"path": "styles/app.css", "content": "body{color:red}"},
            ],
        )
        return history, version

    def test_save_source_updates_current_version_in_place(self):
        history, version = self._project()
        response = self.client.post(
            f"/api/history/{history.id}/versions/{version.id}/source/",
            data=json.dumps(
                {
                    "files": [
                        {"path": "index.html", "content": "<html><body>new</body></html>"},
                        {"path": "styles/app.css", "content": "body{color:blue}"},
                    ],
                    "html_code": "<html><body>new preview</body></html>",
                }
            ),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 200)
        version.refresh_from_db()
        history.refresh_from_db()
        self.assertEqual(version.files[1]["content"], "body{color:blue}")
        self.assertEqual(version.html_code, "<html><body>new preview</body></html>")
        self.assertEqual(history.html_code, "<html><body>new preview</body></html>")
        self.assertEqual(GenerateHistory.objects.get(pk=history.pk).versions.count(), 1)

    def test_save_source_rejects_path_traversal(self):
        history, version = self._project()
        response = self.client.post(
            f"/api/history/{history.id}/versions/{version.id}/source/",
            data=json.dumps(
                {
                    "files": [{"path": "../evil.py", "content": "bad"}],
                    "html_code": "<html></html>",
                }
            ),
            content_type="application/json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(response.status_code, 400)


class VersionTreeDataTests(TestCase):
    def test_version_payload_includes_base_version(self):
        history = GenerateHistory.objects.create(
            prompt="tree test", html_code="<html></html>", project_type="frontend"
        )
        first = GenerateVersion.objects.create(
            history=history, version_no=1, html_code="<html>v1</html>"
        )
        second = GenerateVersion.objects.create(
            history=history,
            version_no=2,
            html_code="<html>v2</html>",
            base_version=first,
        )
        response = self.client.get(
            f"/api/history/{history.id}/", HTTP_HOST="localhost"
        )
        self.assertEqual(response.status_code, 200)
        version = next(
            item for item in response.json()["history"]["versions"]
            if item["id"] == second.id
        )
        self.assertEqual(version["base_version_id"], first.id)
        self.assertEqual(version["base_version_no"], 1)
