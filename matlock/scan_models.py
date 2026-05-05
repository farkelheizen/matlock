"""
Pydantic models for the scan-projects command.

ScannedFile  — one instance per Markdown file that has frontmatter and appears
               to be a project-definition or project-linked page.

ProjectCandidate — one instance per discovered project_id; aggregates all
                   ScannedFile objects associated with that project.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from matlock.config import ResourceConfig


class ScannedFile(BaseModel):
    """Represents a single scanned Markdown file with frontmatter."""

    file_path: str
    """Relative path from base_directory."""

    tagged_project: bool = False
    """True when the file's frontmatter marks it as a project page via tag/tags."""

    named_file_project: str | None = None
    """Leading portion of a filename that ends with ', Project.md' or ', Master Project.md'."""

    projects_directory_project: str | None = None
    """project_name when file_path matches [Pp]rojects/<project_name>/<project_name>.md."""

    project_directory: ResourceConfig | None = None
    """DIRECTORY ResourceConfig for the project; set only on the first candidate for a project_id."""

    project_id: str | None = None
    """
    Resolved project identifier.
    Priority: projects_directory_project → named_file_project → (tagged_project → filename stem).
    """

    super_project_id: str | None = None
    """From the super_project_id frontmatter attribute."""

    title: str | None = None
    """From the title frontmatter attribute."""

    priority: str | None = None
    """From the priority frontmatter attribute; normalised to Low/Medium/High."""

    start_date: str | None = None
    """YYYY-MM-DD from started/start_date/start date; falls back to file creation date."""

    due_date: str | None = None
    """YYYY-MM-DD from due_date/due date/est_comp/est comp."""

    status: str | None = None
    """From the status frontmatter attribute; case-sensitive domain."""

    resources: list[ResourceConfig] = Field(default_factory=list)
    """File/directory resources from the 'resources' and 'related' frontmatter attributes."""

    warnings: list[str] = Field(default_factory=list)
    """Accumulated scan warnings for this file."""

    projects: list[str] = Field(default_factory=list)
    """Distinct project IDs from 'projects'/'Projects' frontmatter attributes."""


class ProjectCandidate(BaseModel):
    """Aggregated view of a single discovered project across all related pages."""

    project_id: str
    """The project identifier."""

    candidates: list[ScannedFile] = Field(default_factory=list)
    """All scanned files identified as home/definition pages for this project.
    The first entry is the primary home page."""

    resources: list[ResourceConfig] = Field(default_factory=list)
    """
    Combined resources for this project:
    - project_directory + frontmatter resources from the first candidate
    - FILE resources for every page whose 'projects' attribute lists this project_id
    """
