from functools import lru_cache
from pathlib import Path

from . import config


@lru_cache(maxsize=1)
def get_db():
    """FalkorDB server when FALKORDB_URL is set, otherwise embedded falkordblite."""
    if config.FALKORDB_URL:
        from falkordb import FalkorDB

        return FalkorDB.from_url(config.FALKORDB_URL)
    try:
        from redislite.falkordb_client import FalkorDB
    except ImportError as e:  # falkordblite is the optional `lite` extra
        raise RuntimeError("FALKORDB_URL is not set and embedded FalkorDB is not installed. "
                           "Set FALKORDB_URL=redis://... or `pip install -e './backend[lite]'`.") from e

    Path(config.FALKORDB_LITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    return FalkorDB(config.FALKORDB_LITE_PATH)


def graph_name(tenant: str | None = None) -> str:
    return f"org_{tenant or config.TENANT}"


def get_graph(tenant: str | None = None):
    return get_db().select_graph(graph_name(tenant))
