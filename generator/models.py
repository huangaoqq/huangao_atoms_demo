from django.db import models


class GenerateHistory(models.Model):
    prompt = models.TextField(verbose_name="用户输入")
    html_code = models.TextField(verbose_name="生成的 HTML")
    backend_language = models.CharField(
        max_length=32, default="java", verbose_name="后端语言"
    )
    backend_code = models.TextField(blank=True, default="", verbose_name="生成的后端代码")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "生成记录"
        verbose_name_plural = "生成记录"

    def __str__(self):
        return f"{self.prompt[:40]} ({self.created_at:%Y-%m-%d %H:%M})"
