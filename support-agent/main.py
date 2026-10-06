"""Entrypoint used by the Agent Manager build: python main.py"""

import uvicorn

from app import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
