"""One SQL warehouse: a long-running DuckDB session serving Quack.

`python -m duckbricks.warehouses.server --id <id> --port <port> --node-type <type>`. The API
passes the catalogs to attach in `DBK_WAREHOUSE_CATALOGS` (JSON), so the warehouse never
reads the control database; catalogs created later are attached by the API's client.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading

from duckbricks.core.config import get_settings
from duckbricks.core.session import CatalogAttachment, open_session, sql_literal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--node-type", default=None)
    args = parser.parse_args()
    settings = get_settings()
    token = settings.warehouse_token.get_secret_value()
    catalogs = [
        CatalogAttachment(**item) for item in json.loads(os.environ["DBK_WAREHOUSE_CATALOGS"])
    ]
    con = open_session(args.node_type, catalogs=catalogs, default_catalog="workspace")
    con.install_extension("quack")
    con.load_extension("quack")
    uri = f"quack:0.0.0.0:{args.port}"
    # Plain HTTP on the private Compose network; the published port binds to 127.0.0.1 only.
    con.execute(
        f"CALL quack_serve({sql_literal(uri)}, token => {sql_literal(token)}, "
        "allow_other_hostname => true, disable_ssl => true)"
    )
    print(f"warehouse {args.id} serving {uri}", flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    con.execute(f"CALL quack_stop({sql_literal(uri)})")
    con.close()


if __name__ == "__main__":
    main()
