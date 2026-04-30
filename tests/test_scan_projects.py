"""Tests for matlock/stages/scan_projects.py — scanner, formatters, merge."""
from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from matlock.config import MatlockConfig, ProjectConfig, ResourceConfig, SuperProjectConfig
from matlock.scan_models import ProjectCandidate, ScannedFile
from matlock.stages.scan_projects import (
    MergeResult,
    _build_project_candidates,
    _scan_file,
    format_as_diff,
    format_as_yaml,
    merge_into_config,
    scan_vault,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> MatlockConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return MatlockConfig(
        base_directory=vault,
        db_path=tmp_path / "matlock.db",
        output_directory=vault / "_Matlock",
    )


def _write_md(path: Path, meta: dict[str, Any], content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True)
    path.write_text(f"---\n{fm}---\n{content}", encoding="utf-8")


def _write_config_yaml(
    config_path: Path,
    base_dir: Path,
    db_path: Path,
    output_dir: Path,
    extra: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {
        "base_directory": str(base_dir),
        "db_path": str(db_path),
        "output_directory": str(output_dir),
    }
    if extra:
        data.update(extra)
    config_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _scan_file — basic cases
# ---------------------------------------------------------------------------


class TestScanFileNone:
    def test_returns_none_for_empty_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "empty.md").write_text("no frontmatter here", encoding="utf-8")
        result = _scan_file(vault, "empty.md", set())
        assert result is None

    def test_returns_none_for_empty_frontmatter(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bare.md").write_text("---\n---\n# heading", encoding="utf-8")
        result = _scan_file(vault, "bare.md", set())
        assert result is None


# ---------------------------------------------------------------------------
# _scan_file — projects_directory_project
# ---------------------------------------------------------------------------


class TestProjectsDirectoryPattern:
    def test_lowercase_projects_dir(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "projects" / "MyProj" / "MyProj.md"
        _write_md(p, {"title": "MyProj"})
        sf = _scan_file(vault, "projects/MyProj/MyProj.md", set())
        assert sf is not None
        assert sf.projects_directory_project == "MyProj"
        assert sf.project_id == "MyProj"

    def test_uppercase_projects_dir(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Projects" / "Alpha" / "Alpha.md"
        _write_md(p, {"title": "Alpha"})
        sf = _scan_file(vault, "Projects/Alpha/Alpha.md", set())
        assert sf is not None
        assert sf.projects_directory_project == "Alpha"
        assert sf.project_id == "Alpha"

    def test_no_match_wrong_filename(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Projects" / "Alpha" / "Notes.md"
        _write_md(p, {"title": "Notes"})
        sf = _scan_file(vault, "Projects/Alpha/Notes.md", set())
        assert sf is not None
        assert sf.projects_directory_project is None

    def test_project_directory_set_on_first_seen(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Projects" / "Alpha" / "Alpha.md"
        _write_md(p, {"title": "Alpha"})
        seen: set[str] = set()
        sf = _scan_file(vault, "Projects/Alpha/Alpha.md", seen)
        assert sf is not None
        assert sf.project_directory is not None
        assert sf.project_directory.type == "DIRECTORY"
        assert sf.project_directory.path == "Projects/Alpha"
        assert "Alpha" in seen

    def test_project_directory_none_on_subsequent_call(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p1 = vault / "Projects" / "Alpha" / "Alpha.md"
        p2 = vault / "Projects" / "Alpha" / "Alpha-v2.md"
        # p2 won't match the directory pattern (different stem), but let's test
        # with seen already containing the project_id
        _write_md(p1, {"title": "Alpha"})
        _write_md(p2, {"title": "Alpha v2", "tag": "project"})
        seen: set[str] = {"Alpha"}
        sf = _scan_file(vault, "Projects/Alpha/Alpha.md", seen)
        assert sf is not None
        assert sf.project_directory is None  # "Alpha" already in seen


# ---------------------------------------------------------------------------
# _scan_file — named_file_project
# ---------------------------------------------------------------------------


class TestNamedFileProject:
    def test_project_suffix(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "My Task, Project.md"
        _write_md(p, {"title": "My Task"})
        sf = _scan_file(vault, "My Task, Project.md", set())
        assert sf is not None
        assert sf.named_file_project == "My Task"
        assert sf.project_id == "My Task"

    def test_master_project_suffix(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Big Initiative, Master Project.md"
        _write_md(p, {"title": "Big Initiative"})
        sf = _scan_file(vault, "Big Initiative, Master Project.md", set())
        assert sf is not None
        assert sf.named_file_project == "Big Initiative"
        assert sf.project_id == "Big Initiative"

    def test_no_match_random_name(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Random Notes.md"
        _write_md(p, {"title": "Random"})
        sf = _scan_file(vault, "Random Notes.md", set())
        assert sf is not None
        assert sf.named_file_project is None

    def test_case_sensitive_suffix(self, tmp_path: Path):
        # Lowercase suffix should NOT match (case-sensitive per Q6)
        vault = tmp_path / "vault"
        p = vault / "My Task, project.md"
        _write_md(p, {"title": "My Task"})
        sf = _scan_file(vault, "My Task, project.md", set())
        assert sf is not None
        assert sf.named_file_project is None


# ---------------------------------------------------------------------------
# _scan_file — tagged_project
# ---------------------------------------------------------------------------


class TestTaggedProject:
    def test_scalar_tag_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "SomeNote.md"
        _write_md(p, {"tag": "Project"})
        sf = _scan_file(vault, "SomeNote.md", set())
        assert sf is not None
        assert sf.tagged_project is True
        assert sf.project_id == "SomeNote"

    def test_scalar_tag_lowercase(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "SomeNote.md"
        _write_md(p, {"tag": "project"})
        sf = _scan_file(vault, "SomeNote.md", set())
        assert sf is not None
        assert sf.tagged_project is True

    def test_list_tags_contains_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "SomeNote.md"
        _write_md(p, {"tags": ["Project", "area", "review"]})
        sf = _scan_file(vault, "SomeNote.md", set())
        assert sf is not None
        assert sf.tagged_project is True

    def test_list_tags_lowercase_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "SomeNote.md"
        _write_md(p, {"tags": ["project"]})
        sf = _scan_file(vault, "SomeNote.md", set())
        assert sf is not None
        assert sf.tagged_project is True

    def test_tag_not_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "SomeNote.md"
        _write_md(p, {"tag": "area"})
        sf = _scan_file(vault, "SomeNote.md", set())
        assert sf is not None
        assert sf.tagged_project is False
        assert sf.project_id is None


# ---------------------------------------------------------------------------
# _scan_file — project_id priority
# ---------------------------------------------------------------------------


class TestProjectIdPriority:
    def test_directory_wins_over_named(self, tmp_path: Path):
        vault = tmp_path / "vault"
        # File matches BOTH directory pattern AND named-file pattern
        p = vault / "Projects" / "Alpha" / "Alpha, Project.md"
        _write_md(p, {"title": "Alpha"})
        sf = _scan_file(vault, "Projects/Alpha/Alpha, Project.md", set())
        assert sf is not None
        # directory pattern: stem is "Alpha, Project" not "Alpha" — won't match
        # named pattern: "Alpha, Project.md" ends with ", Project.md" → "Alpha"
        # directory pattern only matches when stem == parent dir name, so no match here
        # named_file_project wins
        assert sf.named_file_project == "Alpha"
        assert sf.project_id == "Alpha"

    def test_directory_wins_when_stem_matches_dir(self, tmp_path: Path):
        vault = tmp_path / "vault"
        # The directory pattern should win over any other
        p = vault / "Projects" / "Beta" / "Beta.md"
        _write_md(p, {"tag": "Project", "title": "Beta"})
        sf = _scan_file(vault, "Projects/Beta/Beta.md", set())
        assert sf is not None
        assert sf.projects_directory_project == "Beta"
        assert sf.project_id == "Beta"

    def test_named_wins_over_tagged(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "My Task, Project.md"
        _write_md(p, {"tag": "Project"})
        sf = _scan_file(vault, "My Task, Project.md", set())
        assert sf is not None
        assert sf.named_file_project == "My Task"
        assert sf.project_id == "My Task"

    def test_tagged_as_last_resort(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "JustANote.md"
        _write_md(p, {"tag": "Project"})
        sf = _scan_file(vault, "JustANote.md", set())
        assert sf is not None
        assert sf.project_id == "JustANote"


# ---------------------------------------------------------------------------
# _scan_file — date fields
# ---------------------------------------------------------------------------


class TestDateFields:
    def test_valid_start_date(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "start_date": "2025-03-15"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert sf.start_date == "2025-03-15"

    def test_started_attribute(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "started": "2025-06-01"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert sf.start_date == "2025-06-01"

    def test_invalid_start_date_appends_warning(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "start_date": "not-a-date"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert any("start_date" in w for w in sf.warnings)
        # Fallback to file creation date — should be a valid YYYY-MM-DD string
        assert sf.start_date is not None
        assert len(sf.start_date) == 10
        assert sf.start_date[4] == "-"

    def test_start_date_fallback_to_creation_date(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"title": "No dates"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        # Should be today or near today
        assert sf.start_date is not None
        parsed = datetime.date.fromisoformat(sf.start_date)
        assert isinstance(parsed, datetime.date)

    def test_valid_due_date(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "due_date": "2025-12-31"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert sf.due_date == "2025-12-31"

    def test_due_date_est_comp_attribute(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "est_comp": "2025-11-01"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert sf.due_date == "2025-11-01"

    def test_invalid_due_date_appends_warning(self, tmp_path: Path):
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        _write_md(p, {"tag": "Project", "due_date": "01/15/2025"})
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert any("due_date" in w for w in sf.warnings)
        assert sf.due_date is None

    def test_yaml_date_object_coerced(self, tmp_path: Path):
        """PyYAML parses bare YYYY-MM-DD values as datetime.date objects."""
        vault = tmp_path / "vault"
        p = vault / "Note.md"
        # Write raw YAML date (no quotes) so PyYAML sees it as a date object
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntag: Project\nstart_date: 2025-04-01\n---\n", encoding="utf-8")
        sf = _scan_file(vault, "Note.md", set())
        assert sf is not None
        assert sf.start_date == "2025-04-01"


# ---------------------------------------------------------------------------
# _scan_file — priority & status
# ---------------------------------------------------------------------------


class TestPriorityAndStatus:
    def test_priority_normalized_uppercase(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "n.md", {"tag": "Project", "priority": "HIGH"})
        sf = _scan_file(vault, "n.md", set())
        assert sf is not None
        assert sf.priority == "High"

    def test_priority_normalized_lowercase(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "n.md", {"tag": "Project", "priority": "medium"})
        sf = _scan_file(vault, "n.md", set())
        assert sf is not None
        assert sf.priority == "Medium"

    def test_invalid_priority_warning(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "n.md", {"tag": "Project", "priority": "Critical"})
        sf = _scan_file(vault, "n.md", set())
        assert sf is not None
        assert sf.priority is None
        assert any("priority" in w.lower() for w in sf.warnings)

    def test_valid_status(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "n.md", {"tag": "Project", "status": "In Progress"})
        sf = _scan_file(vault, "n.md", set())
        assert sf is not None
        assert sf.status == "In Progress"

    def test_invalid_status_warning(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "n.md", {"tag": "Project", "status": "Active"})
        sf = _scan_file(vault, "n.md", set())
        assert sf is not None
        assert sf.status is None
        assert any("status" in w.lower() for w in sf.warnings)


# ---------------------------------------------------------------------------
# _scan_file — resources / related
# ---------------------------------------------------------------------------


class TestResources:
    def test_resources_from_resources_key(self, tmp_path: Path):
        vault = tmp_path / "vault"
        ref = vault / "Notes" / "ref.md"
        ref.parent.mkdir(parents=True)
        ref.write_text("# ref", encoding="utf-8")
        _write_md(vault / "p.md", {"tag": "Project", "resources": ["Notes/ref.md"]})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert len(sf.resources) == 1
        assert sf.resources[0].type == "FILE"
        assert sf.resources[0].path == "Notes/ref.md"

    def test_resources_from_related_key(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        ref = vault / "related.md"
        ref.write_text("# related", encoding="utf-8")
        _write_md(vault / "p.md", {"tag": "Project", "related": ["related.md"]})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert len(sf.resources) == 1
        assert sf.resources[0].path == "related.md"

    def test_resources_deduped_across_keys(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        ref = vault / "shared.md"
        ref.write_text("# shared", encoding="utf-8")
        _write_md(
            vault / "p.md",
            {"tag": "Project", "resources": ["shared.md"], "related": ["shared.md"]},
        )
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert len(sf.resources) == 1

    def test_directory_resource(self, tmp_path: Path):
        vault = tmp_path / "vault"
        d = vault / "Area"
        d.mkdir(parents=True)
        _write_md(vault / "p.md", {"tag": "Project", "resources": ["Area"]})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert len(sf.resources) == 1
        assert sf.resources[0].type == "DIRECTORY"

    def test_nonexistent_resource_warning(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "resources": ["ghost.md"]})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert len(sf.resources) == 0
        assert any("ghost.md" in w for w in sf.warnings)


# ---------------------------------------------------------------------------
# _scan_file — projects attribute
# ---------------------------------------------------------------------------


class TestProjectsAttribute:
    def test_projects_list_collected(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "note.md", {"projects": ["Alpha", "Beta"]})
        sf = _scan_file(vault, "note.md", set())
        assert sf is not None
        assert sf.projects == ["Alpha", "Beta"]

    def test_projects_case_variant_both_collected(self, tmp_path: Path):
        """Both 'projects' and 'Projects' keys in frontmatter are combined."""
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        # Write raw YAML with both keys (python-frontmatter will see both)
        content = "---\nprojects:\n  - Alpha\nProjects:\n  - Beta\n---\n"
        (vault / "note.md").write_text(content, encoding="utf-8")
        sf = _scan_file(vault, "note.md", set())
        assert sf is not None
        assert "Alpha" in sf.projects
        assert "Beta" in sf.projects

    def test_super_project_id_populated(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "super_project_id": "sp1"})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.super_project_id == "sp1"

    def test_title_populated(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "title": "My Title"})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.title == "My Title"


# ---------------------------------------------------------------------------
# _scan_file — parent super-project inference
# ---------------------------------------------------------------------------


class TestParentSuperProject:
    def test_plain_link(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "parent": "[[IT Learning, Super-Project]]"})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.super_project_id == "IT Learning"

    def test_aliased_link(self, tmp_path: Path):
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "parent": "[[IT Learning, Super-Project|IT Learning]]"})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.super_project_id == "IT Learning"

    def test_explicit_super_project_id_wins(self, tmp_path: Path):
        """Explicit super_project_id takes precedence over parent link."""
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {
            "tag": "Project",
            "super_project_id": "Explicit SP",
            "parent": "[[Other, Super-Project]]",
        })
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.super_project_id == "Explicit SP"

    def test_non_super_project_link_ignored(self, tmp_path: Path):
        """parent link that doesn't end with ', Super-Project' is ignored."""
        vault = tmp_path / "vault"
        _write_md(vault / "p.md", {"tag": "Project", "parent": "[[Some Other Page]]"})
        sf = _scan_file(vault, "p.md", set())
        assert sf is not None
        assert sf.super_project_id is None

    def test_non_project_page_not_populated(self, tmp_path: Path):
        """parent super-project extraction only applies to project candidates."""
        vault = tmp_path / "vault"
        # Has a valid parent link but is NOT a project candidate (no tag, no naming match)
        _write_md(vault / "plain.md", {"parent": "[[IT Learning, Super-Project]]", "title": "Notes"})
        sf = _scan_file(vault, "plain.md", set())
        assert sf is not None
        assert sf.project_id is None
        assert sf.super_project_id is None


# ---------------------------------------------------------------------------
# _build_project_candidates
# ---------------------------------------------------------------------------


class TestBuildProjectCandidates:
    def _rc(self, t: str, p: str) -> ResourceConfig:
        return ResourceConfig(type=t, path=p)  # type: ignore[arg-type]

    def test_single_candidate(self):
        sf = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            project_directory=self._rc("DIRECTORY", "Projects/Alpha"),
        )
        candidates = _build_project_candidates([sf])
        assert len(candidates) == 1
        assert candidates[0].project_id == "Alpha"
        assert len(candidates[0].candidates) == 1
        assert any(r.type == "DIRECTORY" for r in candidates[0].resources)

    def test_multiple_candidates_same_project(self):
        sf1 = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            project_directory=self._rc("DIRECTORY", "Projects/Alpha"),
        )
        sf2 = ScannedFile(file_path="Projects/Alpha/Notes.md", project_id="Alpha")
        candidates = _build_project_candidates([sf1, sf2])
        assert len(candidates) == 1
        assert len(candidates[0].candidates) == 2

    def test_project_directory_only_from_first_candidate(self):
        # First file has project_directory; second doesn't (seen_project_ids already has it)
        sf1 = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            project_directory=self._rc("DIRECTORY", "Projects/Alpha"),
        )
        sf2 = ScannedFile(
            file_path="Projects/Alpha/Extra.md",
            project_id="Alpha",
            project_directory=None,  # already seen in scan_vault
        )
        candidates = _build_project_candidates([sf1, sf2])
        dir_resources = [r for r in candidates[0].resources if r.type == "DIRECTORY"]
        assert len(dir_resources) == 1  # not doubled

    def test_projects_attribute_adds_file_resource(self):
        # Project page
        home = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            project_directory=self._rc("DIRECTORY", "Projects/Alpha"),
        )
        # Non-project page that references Alpha
        linked = ScannedFile(file_path="Notes/linked.md", projects=["Alpha"])
        candidates = _build_project_candidates([home, linked])
        assert len(candidates) == 1
        file_resources = [r for r in candidates[0].resources if r.type == "FILE"]
        assert any(r.path == "Notes/linked.md" for r in file_resources)

    def test_projects_attribute_on_project_page_itself(self):
        """A project page can also list itself/another project via 'projects'."""
        home = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            projects=["Beta"],
        )
        beta = ScannedFile(file_path="Projects/Beta/Beta.md", project_id="Beta")
        candidates = _build_project_candidates([home, beta])
        beta_candidate = next(c for c in candidates if c.project_id == "Beta")
        assert any(r.path == "Projects/Alpha/Alpha.md" for r in beta_candidate.resources)

    def test_sorted_by_project_id(self):
        sf1 = ScannedFile(file_path="z.md", project_id="Zebra")
        sf2 = ScannedFile(file_path="a.md", project_id="Apple")
        sf3 = ScannedFile(file_path="m.md", project_id="mango")
        candidates = _build_project_candidates([sf1, sf2, sf3])
        ids = [c.project_id for c in candidates]
        assert ids == sorted(ids, key=str.lower)

    def test_no_project_id_ignored(self):
        sf = ScannedFile(file_path="Notes/random.md")
        candidates = _build_project_candidates([sf])
        assert candidates == []

    def test_resources_deduped_when_project_page_lists_itself(self):
        """No duplicate FILE resource when project page is in its own resources."""
        rc = ResourceConfig(type="FILE", path="Projects/Alpha/Alpha.md")  # type: ignore[arg-type]
        home = ScannedFile(
            file_path="Projects/Alpha/Alpha.md",
            project_id="Alpha",
            resources=[rc],
            projects=["Alpha"],
        )
        candidates = _build_project_candidates([home])
        file_resources = [r for r in candidates[0].resources if r.path == "Projects/Alpha/Alpha.md"]
        assert len(file_resources) == 1


# ---------------------------------------------------------------------------
# scan_vault (integration)
# ---------------------------------------------------------------------------


class TestScanVault:
    def test_basic_scan(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        vault = cfg.base_directory
        _write_md(vault / "Projects" / "X" / "X.md", {"title": "X"})
        scanned, candidates = scan_vault(cfg)
        assert len(scanned) == 1
        assert len(candidates) == 1
        assert candidates[0].project_id == "X"

    def test_excludes_output_directory(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        vault = cfg.base_directory
        _write_md(vault / "note.md", {"title": "note"})
        # File inside output dir — should be excluded
        _write_md(vault / "_Matlock" / "generated.md", {"title": "gen"})
        scanned, _ = scan_vault(cfg)
        paths = [sf.file_path for sf in scanned]
        assert not any("_Matlock" in p for p in paths)

    def test_excludes_ignored_dirs(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = MatlockConfig(
            base_directory=vault,
            db_path=tmp_path / "matlock.db",
            output_directory=vault / "_out",
            ignore_dirs=[".git"],
        )
        _write_md(vault / ".git" / "COMMIT_EDITMSG.md", {"title": "git"})
        _write_md(vault / "note.md", {"title": "note"})
        scanned, _ = scan_vault(cfg)
        assert all(".git" not in sf.file_path for sf in scanned)

    def test_alphabetical_order(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        vault = cfg.base_directory
        _write_md(vault / "zzz.md", {"title": "z"})
        _write_md(vault / "aaa.md", {"title": "a"})
        _write_md(vault / "mmm.md", {"title": "m"})
        scanned, _ = scan_vault(cfg)
        paths = [sf.file_path for sf in scanned]
        assert paths == sorted(paths, key=str.lower)

    def test_no_frontmatter_files_excluded(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        vault = cfg.base_directory
        (vault / "plain.md").write_text("# no frontmatter", encoding="utf-8")
        _write_md(vault / "fancy.md", {"title": "has frontmatter"})
        scanned, _ = scan_vault(cfg)
        assert len(scanned) == 1
        assert scanned[0].file_path == "fancy.md"


# ---------------------------------------------------------------------------
# format_as_yaml
# ---------------------------------------------------------------------------


class TestFormatAsYaml:
    def _make_candidate(
        self,
        project_id: str,
        title: str | None = None,
        file_path: str | None = None,
        super_project_id: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        resources: list[ResourceConfig] | None = None,
    ) -> ProjectCandidate:
        fp = file_path or f"Projects/{project_id}/{project_id}.md"
        sf = ScannedFile(
            file_path=fp,
            project_id=project_id,
            title=title,
            super_project_id=super_project_id,
            priority=priority,
            status=status,
            start_date=start_date,
            due_date=due_date,
        )
        return ProjectCandidate(
            project_id=project_id,
            candidates=[sf],
            resources=resources or [],
        )

    def test_output_has_both_keys(self):
        pc = self._make_candidate("Alpha")
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        assert "super_projects" in data
        assert "projects" in data

    def test_valid_yaml(self):
        pc = self._make_candidate("Alpha", title="Alpha Project")
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        assert isinstance(data, dict)

    def test_project_fields(self):
        rc = ResourceConfig(type="DIRECTORY", path="Projects/Alpha")  # type: ignore[arg-type]
        pc = self._make_candidate(
            "Alpha",
            title="Alpha Project",
            super_project_id="sp1",
            priority="High",
            status="In Progress",
            start_date="2025-01-01",
            due_date="2025-12-31",
            resources=[rc],
        )
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        proj = data["projects"][0]
        assert proj["id"] == "Alpha"
        assert proj["title"] == "Alpha Project"
        assert proj["super_project_id"] == "sp1"
        assert proj["priority"] == "High"
        assert proj["status"] == "In Progress"
        assert "resources" in proj

    def test_super_projects_derived_from_candidates(self):
        pc = self._make_candidate("Alpha", super_project_id="MySP")
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        assert len(data["super_projects"]) == 1
        assert data["super_projects"][0]["id"] == "MySP"

    def test_empty_candidates(self):
        output = format_as_yaml([], [])
        data = yaml.safe_load(output)
        assert data["super_projects"] == []
        assert data["projects"] == []

    def test_omits_none_fields(self):
        pc = self._make_candidate("Alpha", title="A")
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        proj = data["projects"][0]
        # Fields not set should be absent
        assert "super_project_id" not in proj
        assert "priority" not in proj
        assert "status" not in proj
        assert "due_date" not in proj

    def test_title_falls_back_to_project_id(self):
        pc = self._make_candidate("MyProj", title=None)
        output = format_as_yaml([pc], [])
        data = yaml.safe_load(output)
        assert data["projects"][0]["title"] == "MyProj"


# ---------------------------------------------------------------------------
# format_as_diff
# ---------------------------------------------------------------------------


class TestFormatAsDiff:
    def _cfg_with_projects(
        self,
        tmp_path: Path,
        projects: list[dict],
        super_projects: list[dict] | None = None,
    ) -> MatlockConfig:
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        return MatlockConfig(
            base_directory=vault,
            db_path=tmp_path / "matlock.db",
            output_directory=vault / "_out",
            projects=[ProjectConfig(**p) for p in projects],
            super_projects=[SuperProjectConfig(**sp) for sp in (super_projects or [])],
        )

    def _candidate(self, pid: str, file_path: str | None = None, **kwargs) -> ProjectCandidate:
        fp = file_path if file_path is not None else f"{pid}.md"
        sf = ScannedFile(file_path=fp, project_id=pid, **kwargs)
        return ProjectCandidate(project_id=pid, candidates=[sf])

    def test_added_project_shown(self, tmp_path: Path):
        cfg = self._cfg_with_projects(tmp_path, [])
        pc = self._candidate("NewProj", title="New")
        diff = format_as_diff([pc], [], cfg)
        assert "Added" in diff
        assert "NewProj" in diff

    def test_removed_project_shown(self, tmp_path: Path):
        cfg = self._cfg_with_projects(
            tmp_path,
            [{"id": "OldProj", "title": "Old Project"}],
        )
        diff = format_as_diff([], [], cfg)
        assert "Removed" in diff
        assert "OldProj" in diff

    def test_changed_project_shown(self, tmp_path: Path):
        cfg = self._cfg_with_projects(
            tmp_path,
            [{"id": "Alpha", "title": "Old Title"}],
        )
        pc = self._candidate("Alpha", title="New Title")
        diff = format_as_diff([pc], [], cfg)
        assert "Changed" in diff
        assert "Alpha" in diff
        assert "Old Title" in diff
        assert "New Title" in diff

    def test_unchanged_project_shown(self, tmp_path: Path):
        cfg = self._cfg_with_projects(
            tmp_path,
            [{"id": "Same", "title": "Same Title", "home_file": "Same.md"}],
        )
        pc = self._candidate("Same", title="Same Title", file_path="Same.md")
        diff = format_as_diff([pc], [], cfg)
        assert "Unchanged" in diff
        assert "Same" in diff

    def test_added_super_project(self, tmp_path: Path):
        cfg = self._cfg_with_projects(tmp_path, [])
        pc = self._candidate("Proj", super_project_id="NewSP")
        diff = format_as_diff([pc], [], cfg)
        assert "Super-projects" in diff
        assert "NewSP" in diff
        assert "Added" in diff

    def test_removed_super_project(self, tmp_path: Path):
        cfg = self._cfg_with_projects(
            tmp_path,
            [],
            super_projects=[{"id": "OldSP", "title": "Old SP"}],
        )
        diff = format_as_diff([], [], cfg)
        assert "Removed" in diff
        assert "OldSP" in diff

    def test_no_candidates_shows_all_removed(self, tmp_path: Path):
        cfg = self._cfg_with_projects(
            tmp_path,
            [{"id": "A", "title": "A"}, {"id": "B", "title": "B"}],
        )
        diff = format_as_diff([], [], cfg)
        assert "Removed" in diff


# ---------------------------------------------------------------------------
# merge_into_config
# ---------------------------------------------------------------------------


class TestMergeIntoConfig:
    def _write_minimal_config(
        self,
        config_path: Path,
        vault: Path,
        tmp_path: Path,
        projects: list[dict] | None = None,
        super_projects: list[dict] | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "base_directory": str(vault),
            "db_path": str(tmp_path / "matlock.db"),
            "output_directory": str(vault / "_out"),
            "log_path": str(tmp_path / "matlock.log"),
        }
        if projects is not None:
            data["projects"] = projects
        if super_projects is not None:
            data["super_projects"] = super_projects
        config_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def _candidate(
        self,
        pid: str,
        title: str | None = None,
        super_project_id: str | None = None,
        resources: list[ResourceConfig] | None = None,
        file_path: str | None = None,
    ) -> ProjectCandidate:
        fp = file_path or f"Projects/{pid}/{pid}.md"
        sf = ScannedFile(
            file_path=fp,
            project_id=pid,
            title=title,
            super_project_id=super_project_id,
            start_date="2025-01-01",
        )
        return ProjectCandidate(
            project_id=pid, candidates=[sf], resources=resources or []
        )

    def test_creates_backup_file(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(config_path, vault, tmp_path)

        pc = self._candidate("Alpha")
        result = merge_into_config(config_path, [pc], [])

        assert Path(result.backup_path).exists()
        assert result.backup_path.endswith(".bak")

    def test_adds_new_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(config_path, vault, tmp_path, projects=[])

        pc = self._candidate("NewProj", title="New Project")
        result = merge_into_config(config_path, [pc], [])

        assert result.projects_added == 1
        with config_path.open() as f:
            data = yaml.safe_load(f)
        project_ids = [p["id"] for p in data.get("projects", [])]
        assert "NewProj" in project_ids

    def test_updates_existing_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(
            config_path, vault, tmp_path,
            projects=[{"id": "Alpha", "title": "Old Title"}],
        )

        pc = self._candidate("Alpha", title="New Title")
        result = merge_into_config(config_path, [pc], [])

        assert result.projects_updated == 1
        with config_path.open() as f:
            data = yaml.safe_load(f)
        proj = next(p for p in data["projects"] if p["id"] == "Alpha")
        assert proj["title"] == "New Title"

    def test_deletes_absent_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(
            config_path, vault, tmp_path,
            projects=[
                {"id": "Keep", "title": "Keep"},
                {"id": "Gone", "title": "Gone"},
            ],
        )

        pc = self._candidate("Keep", title="Keep")
        result = merge_into_config(config_path, [pc], [])

        assert result.projects_deleted == 1
        with config_path.open() as f:
            data = yaml.safe_load(f)
        project_ids = [p["id"] for p in data["projects"]]
        assert "Keep" in project_ids
        assert "Gone" not in project_ids

    def test_preserves_unrelated_config_keys(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(config_path, vault, tmp_path)

        result = merge_into_config(config_path, [], [])

        with config_path.open() as f:
            data = yaml.safe_load(f)
        assert "base_directory" in data
        assert "db_path" in data
        assert "log_path" in data

    def test_adds_new_super_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(config_path, vault, tmp_path, super_projects=[])

        pc = self._candidate("Proj", title="Proj", super_project_id="MySP")
        result = merge_into_config(config_path, [pc], [])

        assert result.super_projects_added == 1
        with config_path.open() as f:
            data = yaml.safe_load(f)
        sp_ids = [sp["id"] for sp in data.get("super_projects", [])]
        assert "MySP" in sp_ids

    def test_deletes_absent_super_project(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(
            config_path, vault, tmp_path,
            super_projects=[{"id": "OldSP", "title": "Old SP"}],
        )

        result = merge_into_config(config_path, [], [])

        assert result.super_projects_deleted == 1
        with config_path.open() as f:
            data = yaml.safe_load(f)
        sp_ids = [sp["id"] for sp in data.get("super_projects", [])]
        assert "OldSP" not in sp_ids

    def test_result_counts_correct(self, tmp_path: Path):
        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(
            config_path, vault, tmp_path,
            projects=[
                {"id": "Update", "title": "Old"},
                {"id": "Delete", "title": "Del"},
            ],
        )

        pc_update = self._candidate("Update", title="New")
        pc_add = self._candidate("Add", title="Add")
        result = merge_into_config(config_path, [pc_update, pc_add], [])

        assert result.projects_added == 1
        assert result.projects_updated == 1
        assert result.projects_deleted == 1

    def test_config_valid_after_merge(self, tmp_path: Path):
        """After merge, the config file can be loaded as a valid MatlockConfig."""
        from matlock.config import load_config

        vault = tmp_path / "vault"
        vault.mkdir()
        config_path = tmp_path / "config.yaml"
        self._write_minimal_config(
            config_path, vault, tmp_path,
            projects=[{"id": "Existing", "title": "Existing"}],
            super_projects=[{"id": "SP1", "title": "SP One"}],
        )

        pc = self._candidate("Existing", title="Existing", super_project_id="SP1")
        merge_into_config(config_path, [pc], [])

        loaded = load_config(config_path)
        assert len(loaded.projects) == 1
        assert loaded.projects[0].id == "Existing"
