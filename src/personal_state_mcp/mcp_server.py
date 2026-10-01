from __future__ import annotations

from .adapters.libre_linkup import LibreLinkUpAdapter
from .adapters.google_health import GoogleHealthAdapter
from .collector import Collector
from .config import load_config
from .service import HealthService
from .storage import StateStore


def build_service() -> HealthService:
    config = load_config()
    store = StateStore(config.db_path)
    adapters = [LibreLinkUpAdapter(config)]
    if config.google_health_enabled:
        adapters.append(GoogleHealthAdapter(config))
    collector = Collector(
        store=store,
        adapters=adapters,
        min_poll_interval_seconds=config.min_poll_interval_seconds,
    )
    return HealthService(config=config, store=store, collector=collector)


try:
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
except Exception:  # pragma: no cover - exercised only when MCP is installed.
    MCPServer = None
    ToolAnnotations = None


if MCPServer is not None:
    mcp = MCPServer(
        "Personal State",
        instructions=(
            "Read-only physiological context for moments when the user seems unusually off. "
            "Do not use for alarms, diagnosis, or treatment automation."
        ),
    )

    def _service() -> HealthService:
        return build_service()

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False) if ToolAnnotations else None

    @mcp.tool(name="health.glucose", annotations=read_only)
    def health_glucose() -> dict:
        """Return the freshest known glucose reading with provenance, freshness, and safety boundaries."""
        return _service().glucose()

    @mcp.tool(name="health.glucose_recent", annotations=read_only)
    def health_glucose_recent(hours: float = 3, limit: int = 96) -> dict:
        """Return recent locally persisted glucose history with gaps, provenance, and freshness."""
        return _service().glucose_recent(hours=hours, limit=limit)

    @mcp.tool(name="health.context", annotations=read_only)
    def health_context() -> dict:
        """Return decision-support context for an agent that notices unusual confusion or inconsistency."""
        return _service().context()

    @mcp.tool(name="health.current_state", annotations=read_only)
    def health_current_state() -> dict:
        """Return a compact current physiological context summary for conversation-quality checks."""
        return _service().current_state()

    @mcp.tool(name="health.watch", annotations=read_only)
    def health_watch() -> dict:
        """Return agent-authorized wearable context with recency, attribution, and sync limits."""
        return _service().watch()

    @mcp.tool(name="health.watch_recent", annotations=read_only)
    def health_watch_recent(
        metric: str,
        hours: float = 24,
        limit: int = 200,
        cursor: str | None = None,
        source: str | None = None,
    ) -> dict:
        """Return paged history for one explicitly agent-authorized watch metric."""
        return _service().watch_recent(metric=metric, hours=hours, limit=limit, cursor=cursor, source=source)
else:
    mcp = None


def main() -> None:
    if mcp is None:
        raise SystemExit('Install the optional MCP dependency first: pip install ".[mcp]"')
    mcp.run()


if __name__ == "__main__":
    main()
