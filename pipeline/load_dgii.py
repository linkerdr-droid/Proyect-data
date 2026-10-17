"""Carga el listado de RNC de la DGII en Supabase (solo personas jurídicas: RNC de 9 dígitos).

Uso:  python pipeline/load_dgii.py            (carga completa)
      python pipeline/load_dgii.py --limit 500  (prueba con 500 filas)
"""
import argparse, io, re, zipfile, datetime, requests
from common import *

DGII_URL = "https://www.dgii.gov.do/app/WebApps/Consultas/RNC/DGII_RNC.zip"
ESTADOS = {"ACTIVO", "SUSPENDIDO", "DADO DE BAJA", "ANULADO", "CANCELADO", "INACTIVO", "RECHAZADO"}


def parse_date(s):
    try:
        return datetime.datetime.strptime(s.strip(), "%d/%m/%Y").date().isoformat()
    except Exception:
        return None


def parse_line(line):
    """Formato esperado (7 columnas): RNC|NOMBRE|NOMBRE_COMERCIAL|CATEGORIA|FECHA|ESTADO|REGIMEN.
    También tolera 6 columnas (sin nombre comercial). Devuelve None si la línea no es una empresa."""
    p = [x.strip() for x in line.split("|")]
    if len(p) < 6:
        return None
    rnc = re.sub(r"\D", "", p[0])
    if len(rnc) != 9:           # 11 dígitos = cédula de persona física: se omite (Ley 172-13)
        return None
    if len(p) >= 7:
        nombre, comercial, activ, fecha, tail = p[1], p[2], p[3], p[4], p[5:7]
    else:
        nombre, comercial, activ, fecha, tail = p[1], "", p[2], p[3], p[4:6]
    estado = next((t for t in tail if t.upper() in ESTADOS), tail[0] if tail else "")
    if not nombre:
        return None
    return {"rnc": rnc, "nombre": nombre, "comercial": comercial or None, "activ": activ or None,
            "fecha": parse_date(fecha), "estado": estado.lower() or None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    need_env()
    try:
        print("Descargando DGII_RNC.zip ...")
        r = requests.get(DGII_URL, timeout=600, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = z.read([n for n in z.namelist() if n.lower().endswith(".txt")][0])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        reg = {}
        for line in text.splitlines():
            d = parse_line(line)
            if d:
                reg[d["rnc"]] = d
            if a.limit and len(reg) >= a.limit:
                break
        print(f"{len(reg)} empresas (RNC de 9 dígitos) leídas.")

        # 1) Vincular empresas cargadas a mano: si el nombre coincide exacto, se les asigna su RNC
        by_name, dup = {}, set()
        for d in reg.values():
            for n in (d["nombre"], d["comercial"]):
                k = norm(n) if n else None
                if k:
                    if k in by_name and by_name[k] != d["rnc"]:
                        dup.add(k)
                    by_name[k] = d["rnc"]
        manual = fetch_all("/companies?select=id,legal_name,trade_name,tax_id&origen=eq.manual")
        manual_rncs, linked, unmatched = set(), 0, []
        for c in manual:
            if c["tax_id"]:
                manual_rncs.add(c["tax_id"]); continue
            hit = next((by_name[k] for k in (norm(c["legal_name"]), norm(c["trade_name"] or "")) if k in by_name and k not in dup), None)
            if hit and hit not in manual_rncs:
                d = reg[hit]
                req("PATCH", f"/companies?id=eq.{c['id']}", json={"tax_id": hit, "status": d["estado"], "incorporation_date": d["fecha"], "activity": d["activ"]},
                    headers={"Prefer": "return=minimal"})
                manual_rncs.add(hit); linked += 1
            else:
                unmatched.append(c["legal_name"])
        print(f"Empresas manuales vinculadas a su RNC: {linked}. Sin coincidencia exacta: {len(unmatched)}")
        if unmatched:
            print("  Revisar a mano (nombre distinto al de la DGII):", "; ".join(unmatched[:30]))

        # 2) Cargar el resto (no pisa filas manuales)
        sid = source_id("DGII: listado de RNC contribuyentes", "dgii", DGII_URL)
        rows = [{
            "id_externo": "rnc-" + d["rnc"], "country": "DO", "tax_id": d["rnc"], "legal_name": d["nombre"],
            "trade_name": d["comercial"], "sector": "Sin clasificar", "activity": d["activ"], "status": d["estado"],
            "incorporation_date": d["fecha"], "source_id": sid, "confidence": 0.95, "origen": "automatico",
        } for d in reg.values() if d["rnc"] not in manual_rncs]
        upsert("companies", rows, "id_externo")
        log_run("dgii", len(rows), "ok", f"vinculadas={linked}")
        print(f"Listo: {len(rows)} empresas cargadas/actualizadas.")
    except Exception as e:
        try: log_run("dgii", 0, "error", str(e))
        except Exception: pass
        raise


if __name__ == "__main__":
    main()
