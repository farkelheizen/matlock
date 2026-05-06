"""
scan-projects stage.

Scans a Matlock vault and reverse-engineers the project / super-project
hierarchy from frontmatter and file/directory naming conventions.

Public API:
    scan_vault(config) -> (list[ScannedFile], list[ProjectCandidate])
    format_as_yaml(candidates, scanned) -> str
    format_as_diff(candidates, scanned, config) -> str
    merge_into_config(config_path, candidates, scanned) -> MergeResult
"""

from __future__ import annotations

import dataclasses
import datetime
import os
import re
import shutil
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from matlock.config import MatlockConfig, ProjectConfig, ResourceConfig
from matlock.scan_models import ProjectCandidate, ScannedFile


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

_PRIORITY_MAP: dict[str, str] = {"low": "Low", "medium": "Medium", "high": "High"}

_VALID_STATUSES: frozenset[str] = frozenset(
    {"Planned", "In Progress", "On Hold", "Complete", "Cancelled"}
)
_STATUS_NORM: dict[str, str] = {s.lower(): s for s in _VALID_STATUSES}

# Matches [Pp]rojects/<name>/<name>.md exactly at the path root.
# The \1 backreference ensures the directory name equals the file stem.
_PROJECTS_DIR_RE = re.compile(r"^[Pp]rojects/([^/]+)/\1\.md$")

# Matches Obsidian wiki-links to super-project pages, e.g.:
#   [[IT Learning, Super-Project]]
#   [[IT Learning, Super-Project|IT Learning]]
# Group 1 captures the page name prefix (the super-project id).
_PARENT_SUPER_RE = re.compile(r"^\[\[(.+?), Super-Project(?:\|[^\]]+)?\]\]$")


# ---------------------------------------------------------------------------
# Result type for --merge
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class MergeResult:
    """Counts produced by a single merge_into_config run."""

    super_projects_added: int = 0
    super_projects_updated: int = 0
    super_projects_deleted: int = 0
    projects_added: int = 0
    projects_updated: int = 0
    projects_deleted: int = 0
    backup_path: str = ""


# ---------------------------------------------------------------------------
# Public: vault scanner
# ---------------------------------------------------------------------------


def scan_vault(
    config: MatlockConfig,
) -> tuple[list[ScannedFile], list[ProjectCandidate]]:
    """Walk the vault and return (scanned_files, project_candidates).

    Files are processed in case-folded alphabetical order, root to leaf.
    Only files that have frontmatter are included in scanned_files.
    """
    base_dir = config.base_directory
    output_dir_resolved = config.output_directory.resolve()
    ignore_set = set(config.ignore_dirs)

    rel_paths: list[str] = []

    for dirpath, dirnames, filenames in os.walk(base_dir, followlinks=False):
        current = Path(dirpath).resolve()

        # Skip output_directory and all its descendants
        try:
            current.relative_to(output_dir_resolved)
            dirnames.clear()
            continue
        except ValueError:
            pass

        # Sort dirnames in-place so os.walk descends alphabetically
        dirnames[:] = sorted(
            (d for d in dirnames if d not in ignore_set),
            key=lambda d: d.lower(),
        )

        for filename in filenames:
            if Path(filename).suffix.lower() == ".md":
                full_path = Path(dirpath) / filename
                rel_paths.append(str(full_path.relative_to(base_dir)))

    # Final case-folded sort across all collected paths
    rel_paths.sort(key=lambda p: p.lower())

    seen_project_ids: set[str] = set()
    scanned_files: list[ScannedFile] = []

    for rel_path in rel_paths:
        try:
            sf = _scan_file(base_dir, rel_path, seen_project_ids)
        except (yaml.YAMLError, UnicodeError, OSError, ValueError) as exc:
            sf = ScannedFile(
                file_path=rel_path,
                warnings=[_format_scan_error(exc)],
            )
        if sf is not None:
            scanned_files.append(sf)

    project_candidates = _build_project_candidates(scanned_files)
    return scanned_files, project_candidates


def _format_scan_error(exc: Exception) -> str:
    """Return a single-line warning for scan failures in one file."""
    detail = " ".join(str(exc).splitlines()).strip() or repr(exc)
    if isinstance(exc, yaml.YAMLError):
        return f"Failed to parse frontmatter YAML: {detail}"
    if isinstance(exc, UnicodeError):
        return f"Failed to read file as UTF-8 text: {detail}"
    if isinstance(exc, OSError):
        return f"Failed to read file: {detail}"
    return f"Failed to scan file: {detail}"


# ---------------------------------------------------------------------------
# Internal: single-file scanner
# ---------------------------------------------------------------------------


def _scan_file(
    base_dir: Path,
    rel_path: str,
    seen_project_ids: set[str],
) -> ScannedFile | None:
    """Parse one Markdown file and return a ScannedFile, or None if no frontmatter."""
    abs_path = base_dir / rel_path
    post = frontmatter.load(str(abs_path))

    if not post.metadata:
        return None

    # Build case-insensitive view; first occurrence of a lower-cased key wins.
    meta: dict[str, Any] = {}
    for k, v in post.metadata.items():
        if k.lower() not in meta:
            meta[k.lower()] = v

    warnings: list[str] = []

    # --- tagged_project ---
    tagged_project = False
    tag_val = meta.get("tag")
    if isinstance(tag_val, str) and tag_val.lower() == "project":
        tagged_project = True
    tags_val = meta.get("tags")
    if isinstance(tags_val, list):
        for t in tags_val:
            if isinstance(t, str) and t.lower() == "project":
                tagged_project = True
                break

    # --- named_file_project ---
    filename = Path(rel_path).name
    named_file_project: str | None = None
    for suffix in (", Master Project.md", ", Project.md"):
        if filename.endswith(suffix):
            named_file_project = filename[: -len(suffix)]
            break

    # --- projects_directory_project ---
    normalized_path = rel_path.replace("\\", "/")
    m = _PROJECTS_DIR_RE.match(normalized_path)
    projects_directory_project: str | None = m.group(1) if m else None

    # --- project_id (priority: directory > named > tagged) ---
    if projects_directory_project:
        project_id: str | None = projects_directory_project
    elif named_file_project:
        project_id = named_file_project
    elif tagged_project:
        project_id = Path(rel_path).stem
    else:
        project_id = None

    # --- project_directory (only for the FIRST candidate per project_id) ---
    project_directory: ResourceConfig | None = None
    if projects_directory_project and project_id and project_id not in seen_project_ids:
        parent_dir = str(Path(rel_path).parent)
        project_directory = ResourceConfig(type="DIRECTORY", path=parent_dir)
        seen_project_ids.add(project_id)
    elif projects_directory_project and project_id:
        # Already seen — mark as seen but do not create a directory resource
        pass

    # --- super_project_id ---
    spid = meta.get("super_project_id")
    super_project_id: str | None = spid if isinstance(spid, str) else None

    # Fallback: infer from `parent: "[[Name, Super-Project]]"` when the file
    # is a project candidate and super_project_id was not set explicitly.
    if super_project_id is None and project_id is not None:
        parent_val = meta.get("parent")
        if isinstance(parent_val, str):
            pm = _PARENT_SUPER_RE.match(parent_val.strip())
            if pm:
                super_project_id = pm.group(1)

    # --- title ---
    title_val = meta.get("title")
    title: str | None = title_val if isinstance(title_val, str) else None

    # --- priority ---
    priority: str | None = None
    pval = meta.get("priority")
    if pval is not None:
        if isinstance(pval, str):
            canonical = _PRIORITY_MAP.get(pval.lower())
            if canonical:
                priority = canonical
            else:
                warnings.append(
                    f"Invalid priority {pval!r} — expected Low, Medium, or High"
                )
        else:
            warnings.append(f"Invalid priority value {pval!r} — expected a string")

    # --- start_date ---
    start_date: str | None = None
    for attr in ("started", "start_date", "start date"):
        val = meta.get(attr)
        if val is not None:
            parsed = _parse_date_value(val, attr, warnings)
            if parsed is not None:
                start_date = parsed
                break

    if start_date is None:
        # Fallback: file creation date in local timezone
        stat = abs_path.stat()
        created_s: float = getattr(stat, "st_birthtime", stat.st_ctime)
        start_date = datetime.datetime.fromtimestamp(created_s).date().isoformat()

    # --- due_date ---
    due_date: str | None = None
    for attr in ("due_date", "due date", "est_comp", "est comp"):
        val = meta.get(attr)
        if val is not None:
            parsed = _parse_date_value(val, attr, warnings)
            if parsed is not None:
                due_date = parsed
                break

    # --- status ---
    status: str | None = None
    sval = meta.get("status")
    if sval is not None:
        if isinstance(sval, str):
            canonical = _STATUS_NORM.get(sval.lower())
            if canonical:
                status = canonical
            else:
                warnings.append(
                    f"Invalid status {sval!r} — expected one of: "
                    + str(sorted(_VALID_STATUSES))
                )
        else:
            warnings.append(f"Invalid status value {sval!r} — expected a string")

    # --- resources (from 'resources' and 'related' frontmatter attributes) ---
    resources: list[ResourceConfig] = []
    seen_resource_paths: set[str] = set()
    for attr_key in ("resources", "related"):
        raw = meta.get(attr_key)
        if isinstance(raw, list):
            for entry in raw:
                path_value: str | None = None
                declared_type: str | None = None

                if isinstance(entry, str):
                    path_value = entry
                elif isinstance(entry, dict):
                    path_raw = entry.get("path")
                    if isinstance(path_raw, str):
                        path_value = path_raw
                    type_raw = entry.get("type")
                    if isinstance(type_raw, str):
                        declared_type = type_raw.upper()
                        if declared_type not in {"FILE", "DIRECTORY"}:
                            warnings.append(
                                f"Resource {entry!r} has invalid type {type_raw!r}; expected FILE or DIRECTORY"
                            )
                            continue

                if path_value is None:
                    continue
                if path_value in seen_resource_paths:
                    continue
                seen_resource_paths.add(path_value)

                target = base_dir / path_value
                if target.is_file():
                    if declared_type in (None, "FILE"):
                        resources.append(ResourceConfig(type="FILE", path=path_value))
                    else:
                        warnings.append(
                            f"Resource {path_value!r} is a file but declared as {declared_type}"
                        )
                elif target.is_dir():
                    if declared_type in (None, "DIRECTORY"):
                        resources.append(ResourceConfig(type="DIRECTORY", path=path_value))
                    else:
                        warnings.append(
                            f"Resource {path_value!r} is a directory but declared as {declared_type}"
                        )
                else:
                    warnings.append(
                        f"Resource {path_value!r} is not a valid file or directory"
                    )

    # --- projects (combine 'projects' and 'Projects' keys before lowercasing) ---
    projects_combined: list[str] = []
    for k, v in post.metadata.items():
        if k.lower() == "projects" and isinstance(v, list):
            projects_combined.extend(v)
    seen_proj: set[str] = set()
    projects: list[str] = []
    for p in projects_combined:
        if isinstance(p, str) and p not in seen_proj:
            projects.append(p)
            seen_proj.add(p)

    return ScannedFile(
        file_path=rel_path,
        tagged_project=tagged_project,
        named_file_project=named_file_project,
        projects_directory_project=projects_directory_project,
        project_directory=project_directory,
        project_id=project_id,
        super_project_id=super_project_id,
        title=title,
        priority=priority,
        start_date=start_date,
        due_date=due_date,
        status=status,
        resources=resources,
        warnings=warnings,
        projects=projects,
    )


# ---------------------------------------------------------------------------
# Internal: project-candidate builder
# ---------------------------------------------------------------------------


def _build_project_candidates(files: list[ScannedFile]) -> list[ProjectCandidate]:
    """Aggregate ScannedFiles into ProjectCandidate objects."""
    # Maps project_id → list of definition pages
    candidates_map: dict[str, list[ScannedFile]] = {}
    # Maps project_id → accumulated ResourceConfig list
    resources_map: dict[str, list[ResourceConfig]] = {}
    # Maps project_id → set of (type, path) already in resources_map
    resource_keys: dict[str, set[tuple[str, str]]] = {}

    # First pass: collect project-definition pages and seed resources
    for sf in files:
        if sf.project_id is None:
            continue
        if sf.project_id not in candidates_map:
            candidates_map[sf.project_id] = []
            resources_map[sf.project_id] = []
            resource_keys[sf.project_id] = set()
            # Seed from the first candidate's project_directory + resources
            if sf.project_directory is not None:
                key = (sf.project_directory.type, sf.project_directory.path)
                if key not in resource_keys[sf.project_id]:
                    resources_map[sf.project_id].append(sf.project_directory)
                    resource_keys[sf.project_id].add(key)
            for rc in sf.resources:
                key = (rc.type, rc.path)
                if key not in resource_keys[sf.project_id]:
                    resources_map[sf.project_id].append(rc)
                    resource_keys[sf.project_id].add(key)
        candidates_map[sf.project_id].append(sf)

    # Second pass: any page whose 'projects' attribute names a project_id
    # adds a FILE resource for itself to that project.
    for sf in files:
        for proj_id in sf.projects:
            if proj_id not in candidates_map:
                continue
            key = ("FILE", sf.file_path)
            if key not in resource_keys[proj_id]:
                resources_map[proj_id].append(
                    ResourceConfig(type="FILE", path=sf.file_path)
                )
                resource_keys[proj_id].add(key)

    # Build sorted ProjectCandidate list
    result = [
        ProjectCandidate(
            project_id=pid,
            candidates=candidates_map[pid],
            resources=resources_map[pid],
        )
        for pid in sorted(candidates_map.keys(), key=str.lower)
    ]
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_date_value(val: Any, attr: str, warnings: list[str]) -> str | None:
    """Parse a frontmatter value as a YYYY-MM-DD date string.

    Handles: str (validated), datetime.datetime (coerced), datetime.date (coerced).
    Returns None and appends a warning on invalid input.
    """
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.date().isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, str):
        if _DATE_RE.fullmatch(val):
            return val
        m = _DATE_MDY_RE.fullmatch(val)
        if m:
            try:
                return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
            except ValueError:
                pass
        warnings.append(
            f"Invalid date {val!r} for {attr!r} — expected YYYY-MM-DD or MM/DD/YYYY"
        )
        return None
    warnings.append(f"Non-string/date value for {attr!r}: {val!r}")
    return None


def _candidate_home_file(pc: ProjectCandidate) -> str | None:
    """Return the canonical home file path for a project candidate set."""
    if not pc.candidates:
        return None
    return pc.candidates[0].file_path


def _candidate_first_non_none(pc: ProjectCandidate, field: str) -> Any:
    """Return the first non-None value of *field* across candidate files."""
    for sf in pc.candidates:
        value = getattr(sf, field, None)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Public: --print-yaml formatter
# ---------------------------------------------------------------------------


def format_as_yaml(
    candidates: list[ProjectCandidate],
    scanned: list[ScannedFile],  # noqa: ARG001 — reserved for future use
) -> str:
    """Return a YAML string with super_projects and projects ready to paste into config.yaml."""
    # Collect distinct super_project IDs from effective candidate values, sorted
    sp_ids: dict[str, None] = {}
    for pc in candidates:
        sp_id = _candidate_first_non_none(pc, "super_project_id")
        if sp_id:
            sp_ids[sp_id] = None

    super_projects_list = [
        {"id": sp_id, "title": ""}
        for sp_id in sorted(sp_ids.keys(), key=str.lower)
    ]

    projects_list = []
    for pc in candidates:
        if not pc.candidates:
            continue
        title = _candidate_first_non_none(pc, "title")
        home_file = _candidate_home_file(pc)
        super_project_id = _candidate_first_non_none(pc, "super_project_id")
        priority = _candidate_first_non_none(pc, "priority")
        status = _candidate_first_non_none(pc, "status")
        start_date = _candidate_first_non_none(pc, "start_date")
        due_date = _candidate_first_non_none(pc, "due_date")
        proj: dict[str, Any] = {"id": pc.project_id}
        proj["title"] = title if title else pc.project_id
        if home_file is not None:
            proj["home_file"] = home_file
        if super_project_id:
            proj["super_project_id"] = super_project_id
        if priority:
            proj["priority"] = priority
        if status:
            proj["status"] = status
        if start_date:
            proj["start_date"] = start_date
        if due_date:
            proj["due_date"] = due_date
        if pc.resources:
            proj["resources"] = [
                {"type": r.type, "path": r.path} for r in pc.resources
            ]
        projects_list.append(proj)

    output: dict[str, Any] = {
        "super_projects": super_projects_list,
        "projects": projects_list,
    }
    return yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Public: --diff formatter
# ---------------------------------------------------------------------------


def format_as_diff(
    candidates: list[ProjectCandidate],
    scanned: list[ScannedFile],  # noqa: ARG001 — reserved for future use
    config: MatlockConfig,
) -> str:
    """Return a human-readable diff between scan results and the current config."""
    lines: list[str] = []

    # ---- Super-projects ----
    config_sp = {sp.id: sp for sp in config.super_projects}
    scan_sp_ids: set[str] = {
        sp_id
        for pc in candidates
        if (sp_id := _candidate_first_non_none(pc, "super_project_id"))
    }

    sp_added = sorted(scan_sp_ids - set(config_sp), key=str.lower)
    sp_removed = sorted(set(config_sp) - scan_sp_ids, key=str.lower)
    sp_unchanged = sorted(scan_sp_ids & set(config_sp), key=str.lower)

    lines.append("=== Super-projects ===")
    if sp_added:
        lines.append("Added:")
        for sid in sp_added:
            lines.append(f"  + {sid}")
    if sp_removed:
        lines.append("Removed:")
        for sid in sp_removed:
            lines.append(f"  - {sid}  (title: {config_sp[sid].title!r})")
    if sp_changed := [s for s in sp_unchanged]:  # all unchanged — show them
        lines.append("Unchanged:")
        for sid in sp_changed:
            lines.append(f"    {sid}")
    if not (sp_added or sp_removed or sp_unchanged):
        lines.append("  (none)")

    lines.append("")

    # ---- Projects ----
    config_proj = {p.id: p for p in config.projects}
    scan_proj = {pc.project_id: pc for pc in candidates}

    proj_added = sorted(set(scan_proj) - set(config_proj), key=str.lower)
    proj_removed = sorted(set(config_proj) - set(scan_proj), key=str.lower)

    proj_changed: list[tuple[str, list[tuple[str, Any, Any]]]] = []
    proj_unchanged: list[str] = []
    for pid in sorted(set(scan_proj) & set(config_proj), key=str.lower):
        diffs = _diff_project_fields(scan_proj[pid], config_proj[pid])
        if diffs:
            proj_changed.append((pid, diffs))
        else:
            proj_unchanged.append(pid)

    lines.append("=== Projects ===")
    if proj_added:
        lines.append("Added:")
        for pid in proj_added:
            pc = scan_proj[pid]
            t = _candidate_first_non_none(pc, "title") if pc.candidates else pid
            lines.append(f"  + {pid}  (title: {t!r})")
    if proj_removed:
        lines.append("Removed:")
        for pid in proj_removed:
            lines.append(f"  - {pid}  (title: {config_proj[pid].title!r})")
    if proj_changed:
        lines.append("Changed:")
        for pid, diffs in proj_changed:
            lines.append(f"  ~ {pid}:")
            for field, old_val, new_val in diffs:
                lines.append(f"      {field}: {old_val!r} \u2192 {new_val!r}")
    if proj_unchanged:
        lines.append("Unchanged:")
        for pid in proj_unchanged:
            lines.append(f"    {pid}")
    if not (proj_added or proj_removed or proj_changed or proj_unchanged):
        lines.append("  (none)")

    return "\n".join(lines)


def _diff_project_fields(
    pc: ProjectCandidate,
    cfg: ProjectConfig,
) -> list[tuple[str, Any, Any]]:
    """Return (field, config_val, scan_val) tuples for fields that differ."""
    if not pc.candidates:
        return []
    title = _candidate_first_non_none(pc, "title")
    super_project_id = _candidate_first_non_none(pc, "super_project_id")
    home_file = _candidate_home_file(pc)
    priority = _candidate_first_non_none(pc, "priority")
    status = _candidate_first_non_none(pc, "status")
    start_date = _candidate_first_non_none(pc, "start_date")
    due_date = _candidate_first_non_none(pc, "due_date")
    diffs: list[tuple[str, Any, Any]] = []

    def _check(field: str, config_val: Any, scan_val: Any) -> None:
        if scan_val is not None and config_val != scan_val:
            diffs.append((field, config_val, scan_val))

    _check("title", cfg.title, title)
    _check("super_project_id", cfg.super_project_id, super_project_id)
    _check("home_file", cfg.home_file, home_file)
    _check("priority", cfg.priority, priority)
    _check("status", cfg.status, status)
    _check("start_date", cfg.start_date, start_date)
    _check("due_date", cfg.due_date, due_date)

    scan_res = sorted((r.type, r.path) for r in pc.resources)
    cfg_res = sorted((r.type, r.path) for r in cfg.resources)
    if scan_res != cfg_res:
        diffs.append(("resources", cfg_res, scan_res))

    return diffs


# ---------------------------------------------------------------------------
# Public: --merge config writer
# ---------------------------------------------------------------------------


def merge_into_config(
    config_path: Path,
    candidates: list[ProjectCandidate],
    scanned: list[ScannedFile],  # noqa: ARG001 — reserved for future use
) -> MergeResult:
    """Merge scan results into the config file (full ins/upd/del sync).

    1. Creates a timestamped .bak copy of the config before writing.
    2. Adds new projects/super_projects found in scan.
    3. Updates existing entries with scanned field values; preserves config-only
       fields for which the scan found no value.
    4. Deletes entries present in config but absent from scan.
    5. Writes the result back to config_path.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    backup_path = config_path.with_name(f"{config_path.name}.{timestamp}.bak")
    shutil.copy2(config_path, backup_path)

    with config_path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    existing_sp: dict[str, dict[str, Any]] = {
        entry["id"]: entry
        for entry in raw.get("super_projects", [])
        if isinstance(entry, dict) and "id" in entry
    }
    existing_proj: dict[str, dict[str, Any]] = {
        entry["id"]: entry
        for entry in raw.get("projects", [])
        if isinstance(entry, dict) and "id" in entry
    }

    # Collect scan-derived super_project IDs
    scan_sp_ids: set[str] = {
        sp_id
        for pc in candidates
        if (sp_id := _candidate_first_non_none(pc, "super_project_id"))
    }
    scan_proj_map = {pc.project_id: pc for pc in candidates}

    result = MergeResult(backup_path=str(backup_path))

    # ---- Super-projects ----
    new_sp_list: list[dict[str, Any]] = []
    for sp_id in sorted(scan_sp_ids, key=str.lower):
        if sp_id in existing_sp:
            new_sp_list.append(existing_sp[sp_id])  # preserve existing (title etc.)
        else:
            new_sp_list.append({"id": sp_id, "title": ""})
            result.super_projects_added += 1

    result.super_projects_deleted = sum(
        1 for sp_id in existing_sp if sp_id not in scan_sp_ids
    )

    # ---- Projects ----
    new_proj_list: list[dict[str, Any]] = []
    for pid in sorted(scan_proj_map.keys(), key=str.lower):
        existing = existing_proj.get(pid)
        new_entry = _build_project_dict(scan_proj_map[pid], existing)
        if existing is None:
            result.projects_added += 1
        elif existing != new_entry:
            result.projects_updated += 1
        new_proj_list.append(new_entry)

    result.projects_deleted = sum(
        1 for pid in existing_proj if pid not in scan_proj_map
    )

    # Write back preserving all non-project/super_project keys
    raw["super_projects"] = new_sp_list
    raw["projects"] = new_proj_list

    with config_path.open("w", encoding="utf-8") as fh:
        yaml.dump(raw, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return result


def _build_project_dict(
    pc: ProjectCandidate,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a project config dict, merging scan data over any existing config entry."""
    first = pc.candidates[0] if pc.candidates else None
    title = _candidate_first_non_none(pc, "title")
    super_project_id = _candidate_first_non_none(pc, "super_project_id")
    priority = _candidate_first_non_none(pc, "priority")
    status = _candidate_first_non_none(pc, "status")
    start_date = _candidate_first_non_none(pc, "start_date")
    due_date = _candidate_first_non_none(pc, "due_date")
    d: dict[str, Any] = dict(existing) if existing else {}

    d["id"] = pc.project_id

    if first:
        # title: use scanned value if present, else keep existing, else fallback to id
        if title is not None:
            d["title"] = title
        elif "title" not in d:
            d["title"] = pc.project_id

        # home_file: always update (first candidate IS the home page)
        d["home_file"] = first.file_path

        # Optional fields: update when scan has a value; remove when scan has None
        # (vault is source of truth — absence in frontmatter means absence in config)
        for field, val in (
            ("super_project_id", super_project_id),
            ("priority", priority),
            ("status", status),
            ("start_date", start_date),
            ("due_date", due_date),
        ):
            if val is not None:
                d[field] = val
            elif field in d:
                del d[field]

    # resources: always replace with scan result (even if empty)
    if pc.resources:
        d["resources"] = [{"type": r.type, "path": r.path} for r in pc.resources]
    elif "resources" in d:
        del d["resources"]

    return d
