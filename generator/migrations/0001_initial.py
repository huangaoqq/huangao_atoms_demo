from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="GenerateHistory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("prompt", models.TextField(verbose_name="用户输入")),
                ("html_code", models.TextField(verbose_name="生成的 HTML")),
                (
                    "backend_language",
                    models.CharField(
                        default="java", max_length=32, verbose_name="后端语言"
                    ),
                ),
                (
                    "backend_code",
                    models.TextField(blank=True, default="", verbose_name="生成的后端代码"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="创建时间"),
                ),
            ],
            options={
                "verbose_name": "生成记录",
                "verbose_name_plural": "生成记录",
                "ordering": ["-created_at"],
            },
        ),
    ]
