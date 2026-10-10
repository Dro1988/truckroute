"""TruckRoute API — entry point. Run: uvicorn app_main:app"""
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import models
from config import get_settings
from database import SessionLocal, engine
from routers import admin, auth, incidents, nav, routes, saved, search, subscription, trips
from routers.trucks import pref_router, router as trucks_router

settings = get_settings()

# Create tables, retrying briefly: on a fresh deploy the database can still
# be provisioning when the web service first boots.
import time  # noqa: E402

for _attempt in range(12):
    try:
        models.Base.metadata.create_all(bind=engine)
        break
    except Exception:
        if _attempt == 11:
            raise
        time.sleep(5)

app = FastAPI(title=f"{settings.APP_NAME} API", version="0.1.0-mvp")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.CORS_ORIGINS == "*" else settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(trucks_router)
app.include_router(pref_router)
app.include_router(incidents.router)
app.include_router(search.router)
app.include_router(routes.router)
app.include_router(trips.router)
app.include_router(saved.router)
app.include_router(nav.router)
app.include_router(subscription.router)
app.include_router(admin.router)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    try:
        db = SessionLocal()
        db.add(models.SystemError(where=f"{request.method} {request.url.path}",
                                  message=str(exc)[:1500]))
        db.commit()
        db.close()
    except Exception:
        pass
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.APP_NAME, "version": "0.1.0-mvp"}


# ---- serve the web dashboard (same backend, same accounts) ----
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
WEB_DIR = os.path.abspath(WEB_DIR)
if os.path.isdir(WEB_DIR):
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="web-assets")

    @app.get("/", include_in_schema=False)
    def web_root():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    @app.get("/install", include_in_schema=False)
    @app.get("/install/", include_in_schema=False)
    def install_page():
        return FileResponse(os.path.join(WEB_DIR, "install.html"))

    @app.get("/TruckRoute.apk", include_in_schema=False)
    def apk_download():
        apk = os.path.join(WEB_DIR, "TruckRoute.apk")
        if not os.path.isfile(apk):
            return JSONResponse(status_code=404, content={"detail": "APK not published yet"})
        return FileResponse(apk, media_type="application/vnd.android.package-archive",
                            filename="TruckRoute.apk")
