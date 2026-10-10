from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Load .env early — some libraries check os.environ directly
load_dotenv()

from app.api.routes import router
from app.core.config import BASE_DIR, get_settings
from app.core.logging import configure_logging
from app.services.audit import init_db

configure_logging()


def _ensure_runtime_dirs() -> None:
    settings = get_settings()
    Path(settings.audit_db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "static").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "templates").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_runtime_dirs()
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    _ensure_runtime_dirs()

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    app.state.templates = templates

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        # Starlette >=0.29: TemplateResponse(request, name, context).
        # Older Starlette: TemplateResponse(name, context). Support both.
        context = {"request": request, "app_name": settings.app_name}
        try:
            return templates.TemplateResponse(request, "index.html", context)
        except TypeError:
            return templates.TemplateResponse("index.html", context)

    return app


app = create_app()
