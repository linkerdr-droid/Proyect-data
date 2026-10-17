# Carga automática de datos

1. Supabase > SQL Editor: ejecutar `04_pipeline.sql`.
2. Crear un repositorio PRIVADO en GitHub y subir esta carpeta tal cual (incluye `.github/`).
3. Repo > Settings > Secrets and variables > Actions > New repository secret:
   - `SUPABASE_URL` = https://wsoecnvaxrenbmuowvaq.supabase.co
   - `SUPABASE_SECRET_KEY` = la clave secret de Supabase
4. Pestaña Actions > "Actualizar datos" > Run workflow (primera corrida manual).
Programación: DGCP cada día, DGII cada lunes.

Prueba local (opcional):
  pip install -r pipeline/requirements.txt
  export SUPABASE_URL=... SUPABASE_SECRET_KEY=...
  python pipeline/load_dgcp.py --inspect
  python pipeline/load_dgii.py --limit 500
