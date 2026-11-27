from django.urls import path

from . import views

app_name = "generator"

urlpatterns = [
    path("", views.index, name="index"),
    path("workspace/", views.workspace, name="workspace"),
    path("api/race/stats/", views.race_stats, name="race-stats"),
    path("api/history/", views.history_list, name="history-list"),
    path("api/history/<int:history_id>/", views.history_detail, name="history-detail"),
    path("api/generate/stream/", views.generate_stream, name="generate-stream"),
    path("api/jobs/start/", views.start_job, name="job-start"),
    path("api/jobs/", views.job_list, name="job-list"),
    path("api/jobs/<uuid:job_id>/", views.job_detail, name="job-detail"),
    path("api/jobs/<uuid:job_id>/delete/", views.delete_job, name="job-delete"),
    path("api/history/<int:history_id>/refine/", views.refine, name="refine"),
    path(
        "api/history/<int:history_id>/versions/<int:version_id>/download/",
        views.download_version,
        name="download-version",
    ),
    path(
        "api/history/<int:history_id>/versions/<int:version_id>/delete/",
        views.delete_version,
        name="delete-version",
    ),
    path(
        "api/history/<int:history_id>/versions/<int:version_id>/publish/",
        views.publish_version,
        name="publish-version",
    ),
    path(
        "api/history/<int:history_id>/versions/<int:version_id>/unpublish/",
        views.unpublish_version,
        name="unpublish-version",
    ),
    path("p/<slug:slug>/", views.published_page, name="published-page"),
]
