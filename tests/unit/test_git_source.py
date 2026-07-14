import subprocess
from pathlib import Path

from agentskill_eval_benchmark_gen.git_source import GitSource


def test_apply_patch_is_scoped_to_nested_fixture_inside_parent_repository(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "parent"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet", str(repository)), check=True)
    root_file = repository / "value.txt"
    root_file.write_text("root\n", encoding="utf-8")
    fixture = repository / "workspace" / "fixture"
    fixture.mkdir(parents=True)
    fixture_file = fixture / "value.txt"
    fixture_file.write_text("before\n", encoding="utf-8")
    patch = b"""diff --git a/value.txt b/value.txt
--- a/value.txt
+++ b/value.txt
@@ -1 +1 @@
-before
+after
"""

    GitSource(repository).apply_patch(fixture, patch)

    assert fixture_file.read_text(encoding="utf-8") == "after\n"
    assert root_file.read_text(encoding="utf-8") == "root\n"
