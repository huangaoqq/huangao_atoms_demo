from django.db import migrations


def backfill_version_base(apps, schema_editor):
    """Best-effort parent link for versions created before the tree field.

    Before version trees existed, every refinement was stored as a new
    version without an explicit parent.  In the demo the normal path is a
    sequential refinement of the latest version, so link each legacy vN
    (N > 1) to the immediately previous version in the same project.
    """
    GenerateHistory = apps.get_model("generator", "GenerateHistory")
    GenerateVersion = apps.get_model("generator", "GenerateVersion")

    for history in GenerateHistory.objects.all().iterator():
        previous_id = None
        versions = (
            GenerateVersion.objects.filter(history_id=history.id)
            .order_by("version_no")
            .values_list("id", "version_no", "base_version_id")
        )
        for version_id, version_no, base_version_id in versions.iterator():
            if version_no > 1 and previous_id and not base_version_id:
                GenerateVersion.objects.filter(pk=version_id).update(
                    base_version_id=previous_id
                )
            previous_id = version_id


class Migration(migrations.Migration):
    dependencies = [
        ("generator", "0006_generateversion_base_version"),
    ]

    operations = [
        migrations.RunPython(backfill_version_base, migrations.RunPython.noop),
    ]
