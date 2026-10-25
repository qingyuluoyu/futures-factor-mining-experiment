"""Explicit columnar IPC entry. Never selected by an input text or startup hook."""
from app.services.stock_research_v2.dataset_process import main


if __name__ == "__main__":
    raise SystemExit(main(columnar=True))
