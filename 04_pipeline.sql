-- 04: soporte para los procesos automáticos. Ejecutar una vez en Supabase > SQL Editor
alter table public.companies add column if not exists activity text;   -- actividad económica (DGII)
create index if not exists companies_origen_idx on public.companies (origen);

-- Bitácora: cada corrida de un proceso deja un registro (para monitoreo)
create table if not exists public.pipeline_runs (
  id bigint generated always as identity primary key,
  source text not null,
  started_at timestamptz not null default now(),
  rows_processed int,
  status text,
  detail text
);
alter table public.pipeline_runs enable row level security;  -- sin políticas: solo la clave secret escribe y lee
