import hashlib
from pathlib import Path

import pytest

from agentskill_eval_real_evidence.execution import RealAgentEvidenceRunner, RealEvidenceError


def test_real_evidence_uses_frozen_skill_metadata_identity(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "metadata.yaml").write_text(
        "name: python-test-generation-v1\nversion: 1.0.0\n",
        encoding="utf-8",
    )

    assert RealAgentEvidenceRunner._skill_identity(skill) == (
        "python-test-generation-v1",
        "1.0.0",
    )


def test_real_evidence_rejects_missing_skill_identity(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "metadata.yaml").write_text("license: Apache-2.0\n", encoding="utf-8")

    with pytest.raises(RealEvidenceError, match="requires a name"):
        RealAgentEvidenceRunner._skill_identity(skill)


def test_real_evidence_builds_hash_bound_process_agent_skill_context(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    content = "# Test Generation\nWrite a regression test.\n"
    (skill / "SKILL.md").write_text(content, encoding="utf-8")

    home_files = RealAgentEvidenceRunner._process_agent_skill_home_files(skill)

    assert set(home_files) == {".agentskill-eval/selected-skill.json"}
    context = home_files[".agentskill-eval/selected-skill.json"]
    assert context == {
        "schema_version": "ase/process-agent-skill/v1alpha1",
        "content": content,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
