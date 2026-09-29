from __future__ import annotations

import argparse
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, TextIOWrapper
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    status TEXT NOT NULL,
    entries_total INTEGER NOT NULL DEFAULT 0,
    entries_indexed INTEGER NOT NULL DEFAULT 0,
    indexed_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    archive_id INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE,
    member_path TEXT NOT NULL,
    service TEXT NOT NULL,
    extension TEXT NOT NULL,
    crc32 INTEGER NOT NULL,
    compressed_size INTEGER NOT NULL,
    uncompressed_size INTEGER NOT NULL,
    content_kind TEXT NOT NULL,
    index_status TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE (archive_id, member_path, crc32)
);
CREATE INDEX IF NOT EXISTS documents_archive_idx ON documents(archive_id);
CREATE INDEX IF NOT EXISTS documents_service_idx ON documents(service);
CREATE VIRTUAL TABLE IF NOT EXISTS takeout_fts USING fts5(
    archive_id UNINDEXED,
    document_id UNINDEXED,
    chunk_index UNINDEXED,
    service,
    member_path,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

TEXT_EXTENSIONS = {
    ".csv", ".eml", ".htm", ".html", ".ics", ".json", ".log", ".mbox",
    ".md", ".text", ".tcx", ".tsv", ".txt", ".vcf", ".xml", ".yaml", ".yml",
}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
EXCLUDED_SERVICES = {"fit"}
CHUNK_CHARS = 200_000
CHUNK_OVERLAP = 500
MAX_OFFICE_BYTES = 64 * 1024 * 1024
COMMIT_EVERY = 100


def default_db_path() -> Path:
    override = os.environ.get("GOOGLE_TAKEOUT_SEARCH_DB")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / ".local" / "share"
    return root / "GoogleTakeoutSearch" / "takeout-search.db"


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.executescript(SCHEMA)
    return connection


def discover_archives(inputs: Iterable[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for item in inputs:
        path = item.expanduser().resolve()
        candidates = path.rglob("takeout*.zip") if path.is_dir() else (path,)
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() == ".zip":
                found[str(candidate).casefold()] = candidate
    return sorted(found.values(), key=lambda value: str(value).casefold())


def service_for(member_path: str) -> str:
    parts = PurePosixPath(member_path).parts
    if parts and parts[0].casefold() == "takeout" and len(parts) > 1:
        return parts[1]
    return parts[0] if parts else "Unknown"


def searchable_name(member_path: str) -> str:
    value = PurePosixPath(member_path).name
    return re.sub(r"[_\-.]+", " ", value).strip()


def _clean_text(value: str) -> str:
    return value.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")


def _stream_text_chunks(source) -> Iterator[str]:
    with TextIOWrapper(source, encoding="utf-8", errors="replace", newline=None) as reader:
        overlap = ""
        while True:
            value = reader.read(CHUNK_CHARS)
            if not value:
                break
            cleaned = _clean_text(overlap + value)
            if cleaned.strip():
                yield cleaned
            overlap = cleaned[-CHUNK_OVERLAP:]


def _office_xml_names(extension: str, names: Iterable[str]) -> list[str]:
    lowered = [(name, name.casefold()) for name in names]
    if extension == ".docx":
        return [name for name, low in lowered if low.startswith("word/") and low.endswith(".xml")]
    if extension == ".pptx":
        return [name for name, low in lowered if low.startswith("ppt/slides/slide") and low.endswith(".xml")]
    return [
        name
        for name, low in lowered
        if low == "xl/sharedstrings.xml" or (low.startswith("xl/worksheets/") and low.endswith(".xml"))
    ]


def _office_text_chunks(raw: bytes, extension: str) -> Iterator[str]:
    with zipfile.ZipFile(BytesIO(raw)) as office:
        text_parts: list[str] = []
        text_size = 0
        for name in _office_xml_names(extension, office.namelist()):
            try:
                root = ET.fromstring(office.read(name))
            except (ET.ParseError, KeyError, RuntimeError):
                continue
            value = " ".join(part.strip() for part in root.itertext() if part.strip())
            if not value:
                continue
            text_parts.append(value)
            text_size += len(value) + 1
            if text_size >= CHUNK_CHARS:
                yield _clean_text("\n".join(text_parts))
                text_parts = []
                text_size = 0
        if text_parts:
            yield _clean_text("\n".join(text_parts))


def text_chunks(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, Iterator[str]]:
    extension = PurePosixPath(info.filename).suffix.casefold()
    if extension in TEXT_EXTENSIONS:
        return "text", _stream_text_chunks(archive.open(info, "r"))
    if extension in OFFICE_EXTENSIONS and info.file_size <= MAX_OFFICE_BYTES:
        return "office", _office_text_chunks(archive.read(info), extension)
    metadata = searchable_name(info.filename)
    return "metadata", iter((metadata,))


@dataclass
class IndexResult:
    archives_seen: int = 0
    archives_skipped: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    documents_excluded: int = 0
    chunks_indexed: int = 0

    def public_dict(self, db_path: Path) -> dict[str, object]:
        return {
            "database": str(db_path),
            "archives_seen": self.archives_seen,
            "archives_skipped": self.archives_skipped,
            "documents_indexed": self.documents_indexed,
            "documents_skipped": self.documents_skipped,
            "documents_failed": self.documents_failed,
            "documents_excluded": self.documents_excluded,
            "chunks_indexed": self.chunks_indexed,
        }


def _prepare_archive(connection: sqlite3.Connection, path: Path) -> tuple[int, bool]:
    stat = path.stat()
    row = connection.execute("SELECT * FROM archives WHERE path = ?", (str(path),)).fetchone()
    if row and row["size_bytes"] == stat.st_size and row["mtime_ns"] == stat.st_mtime_ns:
        return int(row["id"]), row["status"] == "complete"
    if row:
        archive_id = int(row["id"])
        connection.execute("DELETE FROM takeout_fts WHERE archive_id = ?", (archive_id,))
        connection.execute("DELETE FROM documents WHERE archive_id = ?", (archive_id,))
        connection.execute(
            """
            UPDATE archives
            SET size_bytes = ?, mtime_ns = ?, status = 'partial', entries_total = 0,
                entries_indexed = 0, indexed_at_utc = NULL
            WHERE id = ?
            """,
            (stat.st_size, stat.st_mtime_ns, archive_id),
        )
    else:
        cursor = connection.execute(
            "INSERT INTO archives(path, size_bytes, mtime_ns, status) VALUES (?, ?, ?, 'partial')",
            (str(path), stat.st_size, stat.st_mtime_ns),
        )
        archive_id = int(cursor.lastrowid)
    connection.commit()
    return archive_id, False


def index_archive(connection: sqlite3.Connection, path: Path, result: IndexResult) -> None:
    archive_id, complete = _prepare_archive(connection, path)
    result.archives_seen += 1
    if complete:
        result.archives_skipped += 1
        return
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        connection.execute(
            "UPDATE archives SET entries_total = ?, status = 'partial' WHERE id = ?",
            (len(infos), archive_id),
        )
        for position, info in enumerate(infos, start=1):
            exists = connection.execute(
                "SELECT 1 FROM documents WHERE archive_id = ? AND member_path = ? AND crc32 = ?",
                (archive_id, info.filename, info.CRC),
            ).fetchone()
            if exists:
                result.documents_skipped += 1
                continue
            extension = PurePosixPath(info.filename).suffix.casefold()
            service = service_for(info.filename)
            if service.casefold() in EXCLUDED_SERVICES:
                result.documents_excluded += 1
                continue
            cursor = connection.execute(
                """
                INSERT INTO documents(
                    archive_id, member_path, service, extension, crc32, compressed_size,
                    uncompressed_size, content_kind, index_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'pending')
                """,
                (
                    archive_id,
                    info.filename,
                    service,
                    extension,
                    info.CRC,
                    info.compress_size,
                    info.file_size,
                ),
            )
            document_id = int(cursor.lastrowid)
            chunk_count = 0
            content_kind = "metadata"
            try:
                content_kind, chunks = text_chunks(archive, info)
                for chunk_index, text_value in enumerate(chunks):
                    if not text_value.strip():
                        continue
                    connection.execute(
                        """
                        INSERT INTO takeout_fts(
                            archive_id, document_id, chunk_index, service, member_path, text
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (archive_id, document_id, chunk_index, service, info.filename, text_value),
                    )
                    chunk_count += 1
                connection.execute(
                    """
                    UPDATE documents
                    SET content_kind = ?, index_status = 'indexed', chunk_count = ?
                    WHERE id = ?
                    """,
                    (content_kind, chunk_count, document_id),
                )
                result.documents_indexed += 1
                result.chunks_indexed += chunk_count
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"[:500]
                connection.execute(
                    "UPDATE documents SET content_kind = ?, index_status = 'failed', error = ? WHERE id = ?",
                    (content_kind, message, document_id),
                )
                connection.execute(
                    """
                    INSERT INTO takeout_fts(
                        archive_id, document_id, chunk_index, service, member_path, text
                    ) VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (archive_id, document_id, service, info.filename, searchable_name(info.filename)),
                )
                result.documents_failed += 1
            if position % COMMIT_EVERY == 0:
                connection.execute(
                    "UPDATE archives SET entries_indexed = ? WHERE id = ?",
                    (position, archive_id),
                )
                connection.commit()
        failed = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE archive_id = ? AND index_status = 'failed'",
            (archive_id,),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE archives
            SET status = ?, entries_indexed = ?, indexed_at_utc = ?
            WHERE id = ?
            """,
            ("complete_with_errors" if failed else "complete", len(infos), utc_now_text(), archive_id),
        )
        connection.commit()


def index_inputs(db_path: Path, inputs: Iterable[Path]) -> dict[str, object]:
    archives = discover_archives(inputs)
    if not archives:
        raise ValueError("No ZIP archives were found in the supplied paths.")
    result = IndexResult()
    with closing(connect(db_path)) as connection:
        for archive in archives:
            index_archive(connection, archive, result)
        services = {
            row["service"]: row["documents"]
            for row in connection.execute(
                "SELECT service, COUNT(*) AS documents FROM documents GROUP BY service ORDER BY service"
            )
        }
        totals = dict(
            connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM archives) AS archives,
                    (SELECT COUNT(*) FROM documents) AS documents,
                    (SELECT COUNT(*) FROM takeout_fts) AS chunks
                """
            ).fetchone()
        )
    payload = result.public_dict(db_path)
    payload["totals"] = totals
    payload["services"] = services
    payload["input_archives"] = [str(path) for path in archives]
    return payload


def _fts_query(value: str) -> str:
    tokens = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    if not tokens:
        raise ValueError("Search query must contain at least one letter or number.")
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def search_database(
    db_path: Path,
    query: str,
    *,
    service: str | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    if not db_path.is_file():
        raise ValueError(f"Search database does not exist: {db_path}")
    sql = """
        SELECT
            a.path AS archive,
            f.service,
            f.member_path,
            CAST(f.chunk_index AS INTEGER) AS chunk_index,
            snippet(takeout_fts, 5, '[', ']', ' ... ', 24) AS snippet,
            bm25(takeout_fts, 8.0, 5.0, 1.0) AS score
        FROM takeout_fts AS f
        JOIN archives AS a ON a.id = CAST(f.archive_id AS INTEGER)
        WHERE takeout_fts MATCH ?
    """
    params: list[object] = [_fts_query(query)]
    if service:
        sql += " AND f.service = ?"
        params.append(service)
    sql += " ORDER BY score LIMIT ?"
    params.append(max(1, min(limit, 200)))
    with closing(connect(db_path)) as connection:
        return [dict(row) for row in connection.execute(sql, params)]


def database_status(db_path: Path) -> dict[str, object]:
    if not db_path.is_file():
        return {"database": str(db_path), "exists": False}
    with closing(connect(db_path)) as connection:
        archives = [dict(row) for row in connection.execute("SELECT * FROM archives ORDER BY path")]
        services = [
            dict(row)
            for row in connection.execute(
                "SELECT service, COUNT(*) AS documents FROM documents GROUP BY service ORDER BY service"
            )
        ]
        chunks = connection.execute("SELECT COUNT(*) FROM takeout_fts").fetchone()[0]
    return {
        "database": str(db_path),
        "exists": True,
        "archives": archives,
        "services": services,
        "chunks": chunks,
    }


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query a local-only Google Takeout search database.")
    parser.add_argument("--db", type=Path, default=default_db_path())
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="Incrementally index one or more Takeout ZIPs or directories.")
    index.add_argument("inputs", nargs="+", type=Path)

    search = subparsers.add_parser("search", help="Full-text search indexed Takeout content.")
    search.add_argument("query")
    search.add_argument("--service")
    search.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("status", help="Show indexed archives and service counts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "index":
            _print(index_inputs(args.db, args.inputs))
        elif args.command == "search":
            _print(search_database(args.db, args.query, service=args.service, limit=args.limit))
        else:
            _print(database_status(args.db))
    except (OSError, ValueError, zipfile.BadZipFile, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
