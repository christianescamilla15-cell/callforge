"""Convenience entry point: `python main.py` (or use uvicorn directly)."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("callforge.presentation.api.app:app", host="0.0.0.0", port=8000, reload=True)
