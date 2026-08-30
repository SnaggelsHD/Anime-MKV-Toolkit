from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import DB_PATH, LIBRARIES_ROOT

app = FastAPI(title="MKV Chapter & Media Info Backup")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "db_path": DB_PATH,
        "libraries_root": LIBRARIES_ROOT,
    }


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
