"""Vault-backed note catalog used by StudyOS projections and adapters.

``StudyNoteCatalog`` is the module interface. The function aliases below it
remain internal implementation helpers so the legacy model-tool adapter can
be migrated without changing its observable contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a project dependency.
    yaml = None  # type: ignore[assignment]


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_CN_NORMALIZE_RE = re.compile(r"[的与和之]")


def _safe_relative_path(vault: Path, rel: str | None) -> Path:
    if not rel or not str(rel).strip():
        return vault
    raw = Path(str(rel).strip())
    candidate = (
        raw.expanduser().resolve()
        if raw.is_absolute()
        else (vault / raw).resolve()
    )
    try:
        candidate.relative_to(vault)
    except ValueError as exc:
        raise ValueError(f"Path escapes vault: {rel}") from exc
    return candidate


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_text_prefix(path: Path, limit: int) -> str:
    """Read at most ``limit`` characters, for files a caller can bound up front.

    A vault file is user-editable and arbitrarily large; a caller that can prove
    it will never look past a given character reads that far and no further,
    instead of paying for the whole file on every call.
    """

    if limit <= 0:
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(limit)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        existing = _read_text(path)
        separator = "" if existing.endswith("\n") else "\n"
        _write_text(path, existing + separator + content)
    else:
        _write_text(path, content)


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str, str | None]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw, None
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}, "\n".join(lines[1:]), "Missing closing --- in frontmatter"
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    if not fm_text.strip():
        return {}, body, None
    if yaml is None:
        return {}, body, "PyYAML unavailable; frontmatter not parsed"
    try:
        parsed = yaml.safe_load(fm_text) or {}
    except Exception as exc:
        return {}, body, f"Failed to parse frontmatter: {exc}"
    if not isinstance(parsed, dict):
        return {}, body, "Frontmatter is not a mapping"
    return parsed, body, None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [str(value)]


def _strip_wikilink(value: str) -> str:
    text = value.strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    if "|" in text:
        text = text.split("|", 1)[0]
    if "#" in text:
        text = text.split("#", 1)[0]
    return text.strip()


def _clean_body_for_links(body: str) -> str:
    return CODE_BLOCK_RE.sub("", body)


def _extract_wikilinks(body: str) -> list[str]:
    links = []
    seen = set()
    for match in WIKILINK_RE.finditer(_clean_body_for_links(body)):
        target = match.group(1).strip()
        if not target or "://" in target or target in seen:
            continue
        seen.add(target)
        links.append(target)
    return links


def _extract_headings(body: str) -> list[dict[str, Any]]:
    headings = []
    clean = CODE_BLOCK_RE.sub("", body)
    for match in HEADING_RE.finditer(clean):
        headings.append(
            {"level": len(match.group(1)), "text": match.group(2).strip()}
        )
    return headings


def _excerpt(body: str, limit: int = 260) -> str:
    clean_lines = []
    for line in CODE_BLOCK_RE.sub("", body).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        clean_lines.append(stripped)
    text = " ".join(clean_lines)
    return text[:limit] + ("..." if len(text) > limit else "")


def _layer_from(path: Path, vault: Path, frontmatter: dict[str, Any]) -> str:
    note_type = str(frontmatter.get("type") or "").strip()
    if note_type:
        return note_type
    relative = path.relative_to(vault).as_posix()
    if "/examples/" in f"/{relative}" or relative.startswith("examples/"):
        return "example"
    if "Box/题型/" in relative or "Box/题型\\" in relative:
        return "pattern"
    if "/Box/" in f"/{relative}" or relative.startswith("Box/"):
        return "concept"
    return "note"


def parse_note(
    path: Path,
    vault: Path,
    include_body: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    raw = _read_text(path)
    frontmatter, body, warning = _parse_frontmatter(raw)
    if warning:
        warnings.append(f"{path.relative_to(vault).as_posix()}: {warning}")
    headings = _extract_headings(body)
    title = str(
        frontmatter.get("title")
        or (headings[0]["text"] if headings else path.stem)
    )
    data: dict[str, Any] = {
        "path": path.relative_to(vault).as_posix(),
        "basename": path.name,
        "title": title,
        "layer": _layer_from(path, vault, frontmatter),
        "frontmatter": frontmatter,
        "tags": _as_list(frontmatter.get("tags")),
        "concepts": [
            _strip_wikilink(value)
            for value in _as_list(frontmatter.get("concepts"))
        ],
        "patterns": [
            _strip_wikilink(value)
            for value in _as_list(frontmatter.get("patterns"))
        ],
        "aliases": _as_list(frontmatter.get("aliases")),
        "headings": headings,
        "wikilinks": _extract_wikilinks(body),
        "excerpt": _excerpt(body),
        "size": path.stat().st_size,
        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(
            timespec="seconds"
        ),
    }
    if include_body:
        data["body"] = body
    return data, warnings


def _iter_markdown_notes(
    vault: Path,
    *,
    folder: str | None = None,
    file_glob: str | None = None,
    include_study_os: bool = False,
) -> Iterable[Path]:
    root = _safe_relative_path(vault, folder)
    if not root.exists():
        return []
    pattern = (file_glob or "**/*.md").strip() or "**/*.md"
    paths = sorted(root.glob(pattern) if root.is_dir() else [root])
    result = []
    for path in paths:
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        relative = path.resolve().relative_to(vault).as_posix()
        parts = Path(relative).parts
        if any(part.startswith(".") for part in parts) and not (
            include_study_os and parts[0] == ".StudyOS"
        ):
            continue
        result.append(path.resolve())
    return result


def _note_subject(note: dict[str, Any]) -> str | None:
    """Return the top-level course folder for a note, when it has one."""
    parts = Path(str(note.get("path") or "")).parts
    if len(parts) < 2 or parts[0].startswith("."):
        return None
    return parts[0]


def _normalize_cn(text: str) -> str:
    return _CN_NORMALIZE_RE.sub("", text.casefold())


def _matches_note(
    note: dict[str, Any],
    *,
    query: str | None,
    tag: str | None,
    layer: str | None,
    search_body: bool = False,
    normalize: bool = False,
) -> bool:
    if layer and note.get("layer") != layer:
        return False
    if tag:
        wanted = tag.strip().lstrip("#")
        tags = {item.lstrip("#") for item in note.get("tags", [])}
        if wanted not in tags:
            return False
    if query:
        query_lower = query.casefold()
        haystacks = [
            str(note.get("path", "")),
            str(note.get("title", "")),
            str(note.get("excerpt", "")),
            " ".join(note.get("aliases", [])),
            " ".join(note.get("concepts", [])),
            " ".join(note.get("patterns", [])),
            " ".join(note.get("wikilinks", [])),
        ]
        if search_body:
            haystacks.append(str(note.get("body", "")))
        lowered = [item.casefold() for item in haystacks]
        if any(query_lower in item for item in lowered):
            return True
        if normalize:
            normalized_query = _normalize_cn(query_lower)
            if normalized_query and any(
                normalized_query in _normalize_cn(item) for item in lowered
            ):
                return True
        return False
    return True


def _find_note(
    vault: Path,
    note_ref: str,
    include_study_os: bool = False,
) -> tuple[Path | None, list[Path]]:
    ref = (note_ref or "").strip()
    if not ref:
        return None, []
    direct = _safe_relative_path(vault, ref)
    if direct.is_file():
        return direct, []
    if direct.with_suffix(".md").is_file():
        return direct.with_suffix(".md"), []
    ref_clean = _strip_wikilink(ref)
    matches = []
    for path in _iter_markdown_notes(vault, include_study_os=include_study_os):
        data, _warnings = parse_note(path, vault, include_body=False)
        candidates = {
            data["path"],
            Path(data["path"]).with_suffix("").as_posix(),
            data["basename"],
            Path(data["basename"]).stem,
            data["title"],
            *data.get("aliases", []),
        }
        if ref_clean in candidates:
            matches.append(path)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def _note_path(vault: Path, value: Any) -> tuple[Path, str]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("note path is required")
    path = _safe_relative_path(vault, raw)
    if not path.suffix:
        path = path.with_suffix(".md")
    if path.suffix.casefold() != ".md":
        raise ValueError(f"StudyOS notes must use the .md extension: {raw}")
    relative = path.relative_to(vault).as_posix()
    if any(part.startswith(".") for part in Path(relative).parts):
        raise ValueError(f"StudyOS notes cannot be saved in hidden directories: {raw}")
    return path, relative


def _note_record_from_raw(vault: Path, path: Path, raw: str) -> dict[str, Any]:
    frontmatter, body, warning = _parse_frontmatter(raw)
    headings = _extract_headings(body)
    title = str(
        frontmatter.get("title")
        or (headings[0]["text"] if headings else path.stem)
    ).strip()
    return {
        "path": path.relative_to(vault).as_posix(),
        "title": title or path.stem,
        "aliases": _as_list(frontmatter.get("aliases")),
        "wikilinks": _extract_wikilinks(body),
        "warning": warning,
    }


def _link_key(value: Any) -> str:
    target = _strip_wikilink(str(value or "")).replace("\\", "/").strip()
    while target.startswith("./"):
        target = target[2:]
    if target.casefold().endswith(".md"):
        target = target[:-3]
    return target.casefold()


def _record_keys(record: dict[str, Any]) -> set[str]:
    relative = str(record["path"])
    path = Path(relative)
    keys = {
        _link_key(relative),
        _link_key(path.with_suffix("").as_posix()),
        _link_key(path.name),
        _link_key(path.stem),
        _link_key(record.get("title")),
    }
    keys.update(_link_key(alias) for alias in record.get("aliases", []))
    return {key for key in keys if key}


def _iter_linkable_assets(vault: Path) -> Iterable[Path]:
    for path in vault.rglob("*"):
        if not path.is_file() or path.suffix.casefold() == ".md":
            continue
        try:
            relative = path.resolve().relative_to(vault)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        yield path.resolve()


def _prepare_note_drafts(
    vault: Path,
    notes: Any,
    *,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(notes, list) or not notes:
        raise ValueError("notes must be a non-empty array")
    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(notes):
        if not isinstance(item, dict):
            raise ValueError(f"notes[{index}] must be an object")
        path, relative = _note_path(vault, item.get("path"))
        if relative.casefold() in seen:
            raise ValueError(f"Duplicate note path in batch: {relative}")
        seen.add(relative.casefold())
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"notes[{index}].content must be a non-empty string")
        may_overwrite = bool(item.get("overwrite", overwrite))
        if path.exists() and not may_overwrite:
            raise FileExistsError(
                f"Note already exists: {relative}; set overwrite=true to update it"
            )
        record = _note_record_from_raw(vault, path, content)
        drafts.append(
            {
                "path": path,
                "relative": relative,
                "content": content,
                "overwrite": may_overwrite,
                "record": record,
            }
        )
    return drafts


def build_wikilink_graph(
    vault: Path,
    *,
    drafts: list[dict[str, Any]] | None = None,
    roots: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build the reachable Obsidian WikiLink graph and report dangling edges.

    Drafts shadow notes at the same path. When drafts are supplied they are the
    default roots, so an unrelated pre-existing dangling link elsewhere in a
    Vault cannot block a focused save. Traversal still follows links through
    existing notes, catching transitive breakage reachable from the new notes.
    """

    vault = vault.resolve()
    drafts = drafts or []
    draft_records = {
        str(item["relative"]): dict(item["record"])
        for item in drafts
    }
    objects: dict[str, dict[str, Any]] = {}
    catalog = StudyNoteCatalog(vault)
    for path in catalog.iter():
        relative = path.relative_to(vault).as_posix()
        if relative in draft_records:
            continue
        note, warnings = catalog.parse(path)
        objects[relative] = {
            "kind": "note",
            "path": relative,
            "title": note["title"],
            "aliases": note.get("aliases", []),
            "wikilinks": note.get("wikilinks", []),
            "warnings": warnings,
        }
    for relative, record in draft_records.items():
        objects[relative] = {
            "kind": "note",
            **record,
            "warnings": [record["warning"]] if record.get("warning") else [],
        }
    for asset in _iter_linkable_assets(vault):
        relative = asset.relative_to(vault).as_posix()
        objects.setdefault(
            relative,
            {
                "kind": "asset",
                "path": relative,
                "title": asset.name,
                "aliases": [],
                "wikilinks": [],
                "warnings": [],
            },
        )

    target_index: dict[str, set[str]] = {}
    for object_id, record in objects.items():
        if record["kind"] == "note":
            keys = _record_keys(record)
        else:
            keys = {
                str(record["path"]).casefold(),
                Path(str(record["path"])).name.casefold(),
            }
        for key in keys:
            target_index.setdefault(key, set()).add(object_id)

    def resolve(source: str, target: str) -> list[str]:
        keys = [_link_key(target)]
        source_parent = Path(source).parent
        if source_parent != Path("."):
            keys.append(_link_key((source_parent / _strip_wikilink(target)).as_posix()))
        matches: set[str] = set()
        for key in keys:
            matches.update(target_index.get(key, set()))
        return sorted(matches)

    if roots is None:
        root_ids = (
            [str(item["relative"]) for item in drafts]
            if drafts
            else sorted(
                object_id
                for object_id, record in objects.items()
                if record["kind"] == "note"
            )
        )
    else:
        root_ids = []
        for root in roots:
            matches = resolve("", str(root))
            root_ids.extend(
                match for match in matches if objects[match]["kind"] == "note"
            )

    queue = list(dict.fromkeys(root_ids))
    visited: set[str] = set()
    edges: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    ambiguous: list[dict[str, Any]] = []
    warnings: list[str] = []
    while queue:
        source = queue.pop(0)
        if source in visited or source not in objects:
            continue
        record = objects[source]
        if record["kind"] != "note":
            continue
        visited.add(source)
        warnings.extend(
            f"{source}: {warning}"
            for warning in record.get("warnings", [])
            if warning
        )
        for target in record.get("wikilinks", []):
            resolved = resolve(source, target)
            if not resolved:
                missing.append({"source": source, "target": target})
                continue
            edge = {"source": source, "target": target, "resolved": resolved}
            edges.append(edge)
            if len(resolved) > 1:
                ambiguous.append(edge)
            for destination in resolved:
                if objects[destination]["kind"] == "note" and destination not in visited:
                    queue.append(destination)

    missing = sorted(
        {
            (item["source"], item["target"]): item
            for item in missing
        }.values(),
        key=lambda item: (item["source"].casefold(), item["target"].casefold()),
    )
    edges.sort(key=lambda item: (item["source"].casefold(), item["target"].casefold()))
    ambiguous.sort(
        key=lambda item: (item["source"].casefold(), item["target"].casefold())
    )
    return {
        "root_notes": list(dict.fromkeys(root_ids)),
        "visited_notes": sorted(visited),
        "node_count": len(visited),
        "edge_count": len(edges),
        "edges": edges,
        "missing": missing,
        "broken_links": missing,
        "ambiguous_links": ambiguous,
        "warnings": sorted(set(warnings)),
    }


def _validate_note_batch(
    vault: Path,
    notes: Any,
    *,
    overwrite: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    drafts = _prepare_note_drafts(vault.resolve(), notes, overwrite=overwrite)
    graph = build_wikilink_graph(vault.resolve(), drafts=drafts)
    return {
        "notes": [
            {
                "path": item["relative"],
                "exists": item["path"].exists(),
                "wikilinks": item["record"]["wikilinks"],
            }
            for item in drafts
        ],
        "graph": graph,
        "missing": graph["missing"],
    }, drafts


def validate_note_batch(
    vault: Path,
    notes: Any,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate a note batch without exposing internal filesystem objects."""

    validation, _drafts = _validate_note_batch(
        vault,
        notes,
        overwrite=overwrite,
    )
    return validation


def _remove_empty_parents(path: Path, vault: Path) -> None:
    parent = path.parent
    while parent != vault:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def save_note_batch(
    vault: Path,
    notes: Any,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Validate and atomically save a recursively closed batch of notes."""

    vault = vault.resolve()
    validation, drafts = _validate_note_batch(
        vault,
        notes,
        overwrite=overwrite,
    )
    if validation["missing"]:
        return {
            "saved": False,
            "notes": validation["notes"],
            "graph": validation["graph"],
            "missing": validation["missing"],
        }

    backups = {
        item["relative"]: (
            item["path"].exists(),
            _read_text(item["path"]) if item["path"].exists() else None,
        )
        for item in drafts
    }
    written: list[dict[str, Any]] = []
    try:
        for item in drafts:
            written.append(item)
            _write_text(item["path"], item["content"])
    except Exception:
        for item in reversed(written):
            existed, content = backups[item["relative"]]
            if existed and content is not None:
                _write_text(item["path"], content)
            else:
                item["path"].unlink(missing_ok=True)
                _remove_empty_parents(item["path"], vault)
        raise

    graph_cache = vault / ".StudyOS" / "concept_graph.json"
    graph_cache.unlink(missing_ok=True)
    return {
        "saved": True,
        "notes": [
            {
                "path": item["relative"],
                "created": not backups[item["relative"]][0],
                "updated": backups[item["relative"]][0],
                "wikilinks": item["record"]["wikilinks"],
            }
            for item in drafts
        ],
        "graph": validation["graph"],
        "missing": [],
    }


@dataclass(frozen=True)
class StudyNoteCatalog:
    """Parse, discover, and resolve notes inside one Vault."""

    vault: Path

    def iter(
        self,
        *,
        folder: str | None = None,
        file_glob: str | None = None,
        include_study_os: bool = False,
    ) -> Iterable[Path]:
        return _iter_markdown_notes(
            self.vault,
            folder=folder,
            file_glob=file_glob,
            include_study_os=include_study_os,
        )

    def parse(
        self,
        path: Path,
        *,
        include_body: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        return parse_note(path, self.vault, include_body=include_body)

    def find(
        self,
        note_ref: str,
        *,
        include_study_os: bool = False,
    ) -> tuple[Path | None, list[Path]]:
        return _find_note(
            self.vault,
            note_ref,
            include_study_os=include_study_os,
        )

    def wikilink_graph(
        self,
        *,
        roots: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        return build_wikilink_graph(self.vault, roots=roots)

    def validate_batch(
        self,
        notes: Any,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return validate_note_batch(
            self.vault,
            notes,
            overwrite=overwrite,
        )

    def save_batch(
        self,
        notes: Any,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return save_note_batch(
            self.vault,
            notes,
            overwrite=overwrite,
        )

    @staticmethod
    def subject(note: dict[str, Any]) -> str | None:
        return _note_subject(note)
