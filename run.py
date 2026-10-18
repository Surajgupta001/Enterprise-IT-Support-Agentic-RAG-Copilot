import os

import uvicorn

if __name__ == "__main__":
    # Reload only app code — watching the whole project dir (incl. .venv)
    # causes the infinite "WatchFiles detected changes in '.venv/...' Reloading..." loop.
    reload = os.getenv("RELOAD", "true").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8080")),
        reload=reload,
        reload_dirs=["app", "templates", "static"] if reload else None,
        reload_excludes=[
            ".venv/*",
            ".git/*",
            "__pycache__/*",
            "*.pyc",
            "data/*",
            "uploads/*",
            "*.db",
            "*.log",
        ]
        if reload
        else None,
    )
