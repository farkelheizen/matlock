"""Tests for matlock/scan_models.py — ScannedFile and ProjectCandidate models."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from matlock.config import ResourceConfig
from matlock.scan_models import ProjectCandidate, ScannedFile


# ---------------------------------------------------------------------------
# ScannedFile
# ---------------------------------------------------------------------------


class TestScannedFileDefaults:
    def test_minimal_construction(self):
        sf = ScannedFile(file_path="Notes/foo.md")
        assert sf.file_path == "Notes/foo.md"
        assert sf.tagged_project is False
        assert sf.named_file_project is None
        assert sf.projects_directory_project is None
        assert sf.project_directory is None
        assert sf.project_id is None
        assert sf.super_project_id is None
        assert sf.title is None
        assert sf.priority is None
        assert sf.start_date is None
        assert sf.due_date is None
        assert sf.status is None
        assert sf.resources == []
        assert sf.warnings == []
        assert sf.projects == []

    def test_file_path_required(self):
        with pytest.raises(ValidationError):
            ScannedFile()  # type: ignore[call-arg]


class TestScannedFileAllFields:
    def _rc(self, t: str, p: str) -> ResourceConfig:
        return ResourceConfig(type=t, path=p)  # type: ignore[arg-type]

    def test_all_fields_populated(self):
        rc = self._rc("FILE", "Notes/foo.md")
        sf = ScannedFile(
            file_path="Projects/Foo/Foo.md",
            tagged_project=True,
            named_file_project=None,
            projects_directory_project="Foo",
            project_directory=self._rc("DIRECTORY", "Projects/Foo"),
            project_id="Foo",
            super_project_id="sp1",
            title="Foo Project",
            priority="High",
            start_date="2025-01-01",
            due_date="2025-12-31",
            status="In Progress",
            resources=[rc],
            warnings=["some warning"],
            projects=["Bar"],
        )
        assert sf.project_id == "Foo"
        assert sf.super_project_id == "sp1"
        assert sf.title == "Foo Project"
        assert sf.priority == "High"
        assert sf.start_date == "2025-01-01"
        assert sf.due_date == "2025-12-31"
        assert sf.status == "In Progress"
        assert len(sf.resources) == 1
        assert sf.resources[0].type == "FILE"
        assert sf.warnings == ["some warning"]
        assert sf.projects == ["Bar"]

    def test_tagged_project_true(self):
        sf = ScannedFile(file_path="x.md", tagged_project=True)
        assert sf.tagged_project is True

    def test_project_directory_resource_config(self):
        rc = self._rc("DIRECTORY", "Projects/Foo")
        sf = ScannedFile(file_path="Projects/Foo/Foo.md", project_directory=rc)
        assert sf.project_directory is not None
        assert sf.project_directory.type == "DIRECTORY"
        assert sf.project_directory.path == "Projects/Foo"


# ---------------------------------------------------------------------------
# project_id resolution — stored value, not logic (logic is in _scan_file)
# ---------------------------------------------------------------------------


class TestScannedFileProjectId:
    """The model itself just stores the resolved project_id; no computation here."""

    def test_project_id_none_by_default(self):
        sf = ScannedFile(file_path="Notes/random.md")
        assert sf.project_id is None

    def test_project_id_set_from_directory_pattern(self):
        sf = ScannedFile(
            file_path="Projects/MyProj/MyProj.md",
            projects_directory_project="MyProj",
            project_id="MyProj",
        )
        assert sf.project_id == "MyProj"

    def test_project_id_set_from_named_file(self):
        sf = ScannedFile(
            file_path="Notes/My Task, Project.md",
            named_file_project="My Task",
            project_id="My Task",
        )
        assert sf.project_id == "My Task"

    def test_project_id_set_from_tagged(self):
        sf = ScannedFile(
            file_path="Notes/SomeNote.md",
            tagged_project=True,
            project_id="SomeNote",
        )
        assert sf.project_id == "SomeNote"


# ---------------------------------------------------------------------------
# ProjectCandidate
# ---------------------------------------------------------------------------


class TestProjectCandidateDefaults:
    def test_minimal_construction(self):
        pc = ProjectCandidate(project_id="my_proj")
        assert pc.project_id == "my_proj"
        assert pc.candidates == []
        assert pc.resources == []

    def test_project_id_required(self):
        with pytest.raises(ValidationError):
            ProjectCandidate()  # type: ignore[call-arg]


class TestProjectCandidatePopulated:
    def test_with_candidates_and_resources(self):
        sf = ScannedFile(file_path="Projects/X/X.md", project_id="X")
        rc = ResourceConfig(type="DIRECTORY", path="Projects/X")  # type: ignore[arg-type]
        pc = ProjectCandidate(project_id="X", candidates=[sf], resources=[rc])
        assert len(pc.candidates) == 1
        assert pc.candidates[0].file_path == "Projects/X/X.md"
        assert len(pc.resources) == 1
        assert pc.resources[0].type == "DIRECTORY"
