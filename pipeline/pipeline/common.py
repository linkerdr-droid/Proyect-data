"""Utilidades compartidas: acceso REST a Supabase con la clave secret."""
import os, sys, time, unicodedata, re, requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/") + "/rest/v1"
KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
HEAD = {"apikey": KEY, "Content-Type": "application/json"}


def need_env():
    if not os.environ.get("SUPABASE_URL") or not KEY:
        sys.exit("Faltan SUPABASE_URL y SUPABASE_SECRET_KEY (variables de entorno).")


def req(method, path, retries=4, **kw):
    for i in range(retries):
        r = requests.request(method, URL + path, headers={**HEAD, **kw.pop("headers", {})}, timeout=120, **kw)
        if r.status_code < 300:
            return r
        if r.status_code in (429, 500, 502, 503, 504) and i < retries - 1:
            time.sleep(2 ** i)
            continue
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")


def upsert(table, rows, conflict, batch=1000):
    """Inserta o actualiza por la columna 'conflict'. Todas las filas deben tener las mismas llaves."""
    for i in range(0, len(rows), batch):
        req("POST", f"/{table}?on_conflict={conflict}", json=rows[i:i + batch],
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})


def fetch_all(path, page=1000):
    out, off = [], 0
    sep = "&" if "?" in path else "?"
    while True:
        chunk = req("GET", f"{path}{sep}limit={page}&offset={off}").json()
        out += chunk
        if len(chunk) < page:
            return out
        off += page


def source_id(name, kind, url):
    upsert("sources", [{"name": name, "kind": kind, "url": url}], "name")
    return req("GET", "/sources", params={"select": "id", "name": f"eq.{name}"}).json()[0]["id"]


def log_run(source, rows, status, detail=""):
    req("POST", "/pipeline_runs", json=[{"source": source, "rows_processed": rows, "status": status, "detail": detail[:500]}],
        headers={"Prefer": "return=minimal"})


def norm(t):
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t.lower()).strip()
