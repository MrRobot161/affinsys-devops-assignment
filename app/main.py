import json
import logging
import os
import sys
import time

import psycopg2
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel


# ---------- Logging: JSON to stdout ----------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": self.formatTime(record),
                "level": record.levelname,
                "message": record.getMessage(),
            }
        )


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger = logging.getLogger("app")
logger.handlers = [handler]
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
logger.propagate = False

# ---------- Metrics ----------
REQUESTS = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
ERRORS = Counter("http_errors_total", "Total HTTP 5xx responses", ["method", "path"])
LATENCY = Histogram(
    "http_request_duration_seconds", "Request latency in seconds", ["method", "path"]
)

app = FastAPI(title="Items API")


# ---------- Database helpers (all config from env vars) ----------
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )


CREATE_TABLE = "CREATE TABLE IF NOT EXISTS items (id SERIAL PRIMARY KEY, name TEXT NOT NULL)"


def db_ping():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    finally:
        conn.close()


def db_create_item(name):
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(CREATE_TABLE)
            cur.execute("INSERT INTO items (name) VALUES (%s) RETURNING id", (name,))
            return {"id": cur.fetchone()[0], "name": name}
    finally:
        conn.close()


def db_list_items():
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(CREATE_TABLE)
            cur.execute("SELECT id, name FROM items ORDER BY id")
            return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


# ---------- Middleware: metrics + request log ----------
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        elapsed = time.perf_counter() - start
        REQUESTS.labels(request.method, path, str(status)).inc()
        LATENCY.labels(request.method, path).observe(elapsed)
        if status >= 500:
            ERRORS.labels(request.method, path).inc()
        logger.info("%s %s %s %.4fs", request.method, path, status, elapsed)


# ---------- Endpoints ----------
class ItemIn(BaseModel):
    name: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        db_ping()
    except Exception:
        logger.exception("readiness check failed")
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ready"}


@app.post("/items", status_code=201)
def create_item(item: ItemIn):
    try:
        return db_create_item(item.name)
    except Exception:
        logger.exception("create item failed")
        raise HTTPException(status_code=503, detail="database error")


@app.get("/items")
def list_items():
    try:
        return db_list_items()
    except Exception:
        logger.exception("list items failed")
        raise HTTPException(status_code=503, detail="database error")


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
