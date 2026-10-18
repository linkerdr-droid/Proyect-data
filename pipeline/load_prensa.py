"""Lee noticias de empresas curadas (origen=manual), extrae hechos con Claude y los guarda con su fuente.
Requiere: SUPABASE_URL, SUPABASE_SECRET_KEY y GEMINI_API_KEY (gratis) o ANTHROPIC_API_KEY.
Uso: python pipeline/load_prensa.py [--probe] [--empresas 5]
Los sitios deben exponer la API estándar de WordPress (/wp-json/wp/v2/posts). --probe verifica cada uno."""
import argparse, json, os, re, html, time, requests
from common import *

SITIOS = ["https://eldinero.com.do", "https://revistamercado.do"]
MODELO = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
TIPOS = {"noticia", "permiso_construccion", "nuevo_proyecto", "expansion", "inversion", "cambio_ejecutivo", "licitacion", "sancion", "otro"}
INV = {"capital", "adquisicion", "fusion", "proyecto", "deuda", "emision", "otro"}
ETAPAS = {"rumor", "anunciado", "planificacion", "licitacion", "ejecucion", "completado"}
PROMPT = """Empresa objetivo: {emp}
Artículo (título y texto):
{txt}

Extrae SOLO hechos que el texto afirma explícitamente sobre la empresa objetivo. No inventes ni infieras. Responde SOLO JSON:
{{"relevante":bool,"eventos":[{{"tipo":"noticia|nuevo_proyecto|expansion|inversion|cambio_ejecutivo|licitacion|sancion|otro","titulo":str,"resumen":str,"etapa":"rumor|anunciado|planificacion|licitacion|ejecucion|completado"|null,"fecha":"YYYY-MM-DD"|null,"monto":number|null,"moneda":"USD|DOP"|null}}],
"inversiones":[{{"destino":str,"tipo":"capital|adquisicion|fusion|proyecto|deuda|emision|otro","monto":number|null,"moneda":"USD|DOP"|null,"fecha":"YYYY-MM-DD"|null,"resumen":str}}],
"personas":[{{"nombre":str,"cargo":str,"desde":"YYYY-MM-DD"|null}}],"confianza":0.0-1.0}}
"inversiones" solo si la empresa objetivo es quien invierte. Si el artículo no trata de la empresa: {{"relevante":false}}."""


def limpio(h): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def buscar(sitio, emp):
    r = requests.get(f"{sitio}/wp-json/wp/v2/posts", timeout=30, headers={"User-Agent": "Mozilla/5.0"},
                     params={"search": emp, "per_page": 8, "_fields": "link,title,date,content"})
    r.raise_for_status()
    return r.json()


LLAMADAS = {"n": 0, "max": int(os.environ.get("MAX_LLAMADAS", "150"))}


def claude(emp, txt):
    """Usa Gemini (plan gratis) si hay GEMINI_API_KEY; si no, Anthropic. Respeta límites: 1 llamada cada ~6 s y tope por corrida."""
    if LLAMADAS["n"] >= LLAMADAS["max"]:
        raise StopIteration("tope de llamadas por corrida alcanzado")
    LLAMADAS["n"] += 1
    prompt = PROMPT.format(emp=emp, txt=txt[:6000])
    g = os.environ.get("GEMINI_API_KEY")
    for intento in range(4):
        if g:
            time.sleep(6)
            m = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", timeout=120,
                headers={"x-goog-api-key": g, "content-type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        else:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODELO, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code == 429 and intento < 3:
            time.sleep(20 * (intento + 1)); continue
        r.raise_for_status()
        j = r.json()
        t = j["candidates"][0]["content"]["parts"][0]["text"] if g else "".join(b.get("text", "") for b in j["content"])
        return json.loads(t[t.index("{"):t.rindex("}") + 1])


def persona(nombre):
    f = req("GET", "/people", params={"select": "id", "name_search": f"eq.{norm(nombre)}", "limit": 1}).json()
    if f: return f[0]["id"]
    return req("POST", "/people", json=[{"full_name": nombre, "country": "DO", "confidence": 0.7}], headers={"Prefer": "return=representation"}).json()[0]["id"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--probe", action="store_true"); ap.add_argument("--empresas", type=int, default=0)
    a = ap.parse_args()
    if a.probe:
        for s in SITIOS:
            try: print(s, "OK" if buscar(s, "banco") else "sin resultados")
            except Exception as e: print(s, "FALLA:", e)
        return
    need_env()
    ne = ni = nr = 0
    try:
        emps = fetch_all("/companies?select=id,legal_name,trade_name&origen=eq.manual&order=legal_name")
        for c in (emps[:a.empresas] if a.empresas else emps):
            nombre = c["trade_name"] or c["legal_name"]
            for sitio in SITIOS:
                try: posts = buscar(sitio, nombre)
                except Exception as e: print("  sitio omitido", sitio, e); continue
                for p in posts:
                    url = p["link"]
                    if req("GET", "/news_seen", params={"select": "url", "url": f"eq.{url}"}).json(): continue
                    titulo = limpio(p["title"]["rendered"]); txt = titulo + ". " + limpio(p["content"]["rendered"])
                    upsert("news_seen", [{"url": url, "company_id": c["id"]}], "url")
                    if norm(nombre) not in norm(txt): continue
                    d = claude(nombre, txt)
                    if not d.get("relevante"): continue
                    sid = source_id(f"{sitio.split('//')[1]}: {titulo}"[:200], "prensa", url)
                    conf = max(0.3, min(float(d.get("confianza") or .6), .9))
                    evs = [{"company_id": c["id"], "event_type": e["tipo"] if e.get("tipo") in TIPOS else "noticia", "title": e["titulo"][:300], "summary": e.get("resumen"),
                            "stage": e.get("etapa") if e.get("etapa") in ETAPAS else None, "occurred_on": e.get("fecha") or p["date"][:10], "estimated_value": e.get("monto"),
                            "currency": (e.get("moneda") or "USD")[:3], "source_id": sid, "confidence": conf} for e in d.get("eventos", [])]
                    if evs: req("POST", "/events", json=evs, headers={"Prefer": "return=minimal"}); ne += len(evs)
                    ivs = [{"investor_company_id": c["id"], "target_description": i["destino"][:300], "investment_type": i["tipo"] if i.get("tipo") in INV else "otro", "amount": i.get("monto"),
                            "currency": (i.get("moneda") or "USD")[:3], "announced_on": i.get("fecha"), "summary": i.get("resumen"), "source_id": sid, "confidence": conf} for i in d.get("inversiones", [])]
                    if ivs: req("POST", "/investments", json=ivs, headers={"Prefer": "return=minimal"}); ni += len(ivs)
                    for x in d.get("personas", []):
                        if x.get("nombre") and x.get("cargo"):
                            req("POST", "/roles", json=[{"person_id": persona(x["nombre"]), "company_id": c["id"], "title": x["cargo"][:200], "start_date": x.get("desde"), "source_id": sid, "confidence": conf}], headers={"Prefer": "return=minimal"}); nr += 1
        log_run("prensa", ne + ni + nr, "ok", f"eventos={ne} inversiones={ni} cargos={nr}")
        print(f"Listo: {ne} eventos, {ni} inversiones, {nr} cargos.")
    except StopIteration:
        log_run("prensa", ne + ni + nr, "parcial", f"tope de llamadas; eventos={ne} inversiones={ni} cargos={nr}"); print("Tope de llamadas alcanzado; continúa en la próxima corrida.")
    except Exception as e:
        try: log_run("prensa", 0, "error", str(e))
        except Exception: pass
        raise

if __name__ == "__main__": main()
