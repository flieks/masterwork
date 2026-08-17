"""Print the OpenAPI schema straight from the FastAPI app object — no server, no HTTP.

Byte-identical to `curl :8008/openapi.json | python3 -m json.tool`, so the offline
dump and a live backend can never disagree about key order or whitespace.
"""

import json
import sys

from app.main import app


def main() -> None:
    sys.stdout.write(json.dumps(app.openapi(), indent=4) + "\n")


if __name__ == "__main__":
    main()
