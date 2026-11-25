from django.urls import path

from . import views

app_name = "generator"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/generate/", views.api_generate, name="api_generate"),
    path("api/select/", views.api_select, name="api_select"),
    path("api/revise/", views.api_revise, name="api_revise"),
    path(
        "api/history/<int:history_id>/delete/",
        views.api_delete_history,
        name="api_delete_history",
    ),
    path(
        "api/history/<int:history_id>/download/",
        views.api_download_history,
        name="api_download_history",
    ),
]
