"""Entry point: `python -m cookbooks.api` → uvicorn on 127.0.0.1:8000."""
from __future__ import annotations

import uvicorn

from cookbooks.api.server import get_host, get_port


def main() -> None:
    host = get_host()
    port = get_port()
    print(f"\033[32mpfh API\033[0m starting on http://{host}:{port}")
    uvicorn.run(
        "cookbooks.api.server:app",
        host=host, port=port,
        reload=False, access_log=False,
    )


if __name__ == "__main__":
    main()
