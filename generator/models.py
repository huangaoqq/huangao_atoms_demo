import uuid

from django.db import models


class GenerateHistory(models.Model):
    """One generation task, with its currently selected HTML version."""

    prompt = models.TextField(verbose_name="用户提示词")
    html_code = models.TextField(verbose_name="当前选中的HTML")
    model_provider = models.CharField(max_length=20, default="deepseek", verbose_name="生成模型")
    project_type = models.CharField(max_length=20, default="frontend", verbose_name="项目类型")
    backend_label = models.CharField(max_length=120, blank=True, verbose_name="后端技术栈")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "生成记录"
        verbose_name_plural = "生成记录"

    def __str__(self) -> str:
        return f"#{self.pk} {self.prompt[:40]}"


class GenerateVersion(models.Model):
    """Immutable snapshots keep every refinement available for comparison."""

    history = models.ForeignKey(
        GenerateHistory,
        on_delete=models.CASCADE,
        related_name="versions",
        verbose_name="生成记录",
    )
    version_no = models.PositiveIntegerField(verbose_name="版本号")
    html_code = models.TextField(verbose_name="HTML代码")
    note = models.TextField(blank=True, verbose_name="版本说明")
    files = models.JSONField(default=list, blank=True, verbose_name="项目文件")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        ordering = ["version_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["history", "version_no"], name="unique_history_version"
            )
        ]
        verbose_name = "生成版本"
        verbose_name_plural = "生成版本"

    def __str__(self) -> str:
        return f"#{self.history_id} v{self.version_no}"


class PublishedPage(models.Model):
    """A version exposed as a standalone route on the current domain."""

    version = models.OneToOneField(
        GenerateVersion,
        on_delete=models.CASCADE,
        related_name="publication",
        verbose_name="发布版本",
    )
    slug = models.CharField(max_length=32, unique=True, verbose_name="访问标识")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="发布时间")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "已发布网页"
        verbose_name_plural = "已发布网页"

    def __str__(self) -> str:
        return f"{self.slug} (#{self.version.history_id} v{self.version.version_no})"


class GenerationJob(models.Model):
    """A background generation task, independent from the page connection."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.CharField(max_length=80, db_index=True, blank=True, verbose_name="浏览器标识")
    prompt = models.TextField(verbose_name="用户提示词")
    requested_mode = models.CharField(max_length=20, default="single", verbose_name="请求模式")
    mode = models.CharField(max_length=20, blank=True, verbose_name="实际模式")
    status = models.CharField(max_length=20, default=STATUS_QUEUED, db_index=True, verbose_name="状态")
    progress = models.PositiveSmallIntegerField(default=0, verbose_name="进度")
    stage_title = models.CharField(max_length=120, blank=True, verbose_name="阶段")
    stage_message = models.TextField(blank=True, verbose_name="阶段说明")
    state = models.JSONField(default=dict, blank=True, verbose_name="任务状态")
    project_type = models.CharField(max_length=20, blank=True, verbose_name="项目类型")
    backend_label = models.CharField(max_length=120, blank=True, verbose_name="后端技术栈")
    history = models.ForeignKey(
        GenerateHistory,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="generation_jobs",
        verbose_name="生成结果",
    )
    error = models.TextField(blank=True, verbose_name="错误信息")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "生成任务"
        verbose_name_plural = "生成任务"

    def __str__(self) -> str:
        return f"{self.id} {self.status} {self.prompt[:30]}"
