"""Carga adjudicaciones de Compras Dominicanas (OCDS, copia del Open Contracting Partnership, licencia ODbL).

Uso:  python pipeline/load_dgcp.py                 (año actual)
      python pipeline/load_dgcp.py --year 2025
      python pipeline/load_dgcp.py --inspect       (muestra la estructura del primer registro y sale)
Solo se guardan adjudicaciones cuyo proveedor se pueda enlazar por RNC a una empresa ya cargada.
"""
import argparse, datetime, gzip, json, re, tempfile, requests
from common import *

PUB = "https://data.open-contracting.org/en/publication/22"


def rnc_of(party):
    """RNC (9 dígitos) de una parte OCDS. Prefiere identificadores cuyo esquema contenga 'RNC'."""
    ids = [party.get("identifier") or {}] + list(party.get("additionalIdentifiers") or [])
    ids.sort(key=lambda i: "RNC" not in str(i.get("scheme", "")).upper())
    for i in ids:
        d = re.sub(r"\D", "", str(i.get("id", "")))
        if len(d) == 9:
            return d
    return None


def awards_of(rel, rnc_to_company, sid):
    parties = {p.get("id"): p for p in rel.get("parties", [])}
    out = []
    for aw in rel.get("awards", []):
        for sup in aw.get("suppliers", []):
            rnc = rnc_of(parties.get(sup.get("id"), sup))
            cid = rnc_to_company.get(rnc)
            if not cid:
                continue
            val = aw.get("value") or {}
            cur = (val.get("currency") or "DOP")[:3]
            out.append({
                "company_id": cid,
                "buyer_name": (rel.get("buyer") or {}).get("name") or "No indicado",
                "reference": f"{rel.get('ocid')}#{aw.get('id')}#{rnc}",
                "title": (rel.get("tender") or {}).get("title") or aw.get("title") or "Sin título",
                "amount": val.get("amount"), "currency": cur,
                "awarded_on": (aw.get("date") or "")[:10] or None,
                "status": aw.get("status"), "source_id": sid,
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=datetime.date.today().year)
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    url = f"{PUB}/download?name={a.year}.jsonl.gz"
    print("Descargando", url)
    with tempfile.NamedTemporaryFile(suffix=".gz") as tmp:
        with requests.get(url, stream=True, timeout=600) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        if a.inspect:
            with gzip.open(tmp.name, "rt", encoding="utf-8") as f:
                rel = json.loads(f.readline())
            print("Claves:", list(rel.keys()))
            print("Parte de ejemplo:", json.dumps((rel.get("parties") or [{}])[0], ensure_ascii=False, indent=1)[:1500])
            print("Adjudicación de ejemplo:", json.dumps((rel.get("awards") or [{}])[0], ensure_ascii=False, indent=1)[:1500])
            return
        need_env()
        try:
            sid = source_id("DGCP: Compras Dominicanas (datos abiertos OCDS)", "compras_dominicanas", PUB)
            print("Leyendo RNC de empresas en la base ...")
            rnc_to_company = {c["tax_id"]: c["id"] for c in fetch_all("/companies?select=id,tax_id&tax_id=not.is.null")}
            print(len(rnc_to_company), "empresas con RNC.")
            buf, total, seen = {}, 0, 0
            with gzip.open(tmp.name, "rt", encoding="utf-8") as f:
                for line in f:
                    seen += 1
                    for row in awards_of(json.loads(line), rnc_to_company, sid):
                        buf[row["reference"]] = row
                    if len(buf) >= 2000:
                        upsert("public_contracts", list(buf.values()), "reference"); total += len(buf); buf = {}
                    if a.limit and seen >= a.limit:
                        break
            if buf:
                upsert("public_contracts", list(buf.values()), "reference"); total += len(buf)
            log_run("dgcp", total, "ok", f"año={a.year}, procesos leídos={seen}")
            print(f"Listo: {total} adjudicaciones enlazadas ({seen} procesos leídos).")
        except Exception as e:
            try: log_run("dgcp", 0, "error", str(e))
            except Exception: pass
            raise


if __name__ == "__main__":
    main()
