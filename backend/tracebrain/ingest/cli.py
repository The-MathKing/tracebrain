"""tracebrain-ingest owner/name [owner/name ...] [--tenant T] [--full] [--max-items N] [--no-code] [--no-decisions]"""

import argparse
import json

from .. import config
from .sync import ingest_repo


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tracebrain-ingest", description="Sync GitHub repos into a TraceBrain graph.")
    ap.add_argument("repos", nargs="+", help="owner/name")
    ap.add_argument("--tenant", default=config.TENANT)
    ap.add_argument("--full", action="store_true", help="ignore SyncState and refetch everything")
    ap.add_argument("--max-items", type=int, default=300)
    ap.add_argument("--no-code", action="store_true")
    ap.add_argument("--no-decisions", action="store_true")
    a = ap.parse_args(argv)
    for repo in a.repos:
        counts = ingest_repo(repo, tenant=a.tenant, max_items=a.max_items, with_code=not a.no_code,
                             with_decisions=not a.no_decisions, full=a.full)
        print(json.dumps({k: v for k, v in counts.items() if k != "graph"}, indent=1))
    print(json.dumps(counts["graph"], indent=1))


if __name__ == "__main__":
    main()
