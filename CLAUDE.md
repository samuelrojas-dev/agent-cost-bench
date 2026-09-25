# Reglas del proyecto
- Nunca inventes resultados, métricas, cobertura ni badges. Todo número sale de una ejecución real y reproducible.
- Nunca escribas API keys en código, logs, tests ni commits. Solo variables de entorno, con .env.example.
- Los tests usan mocks de proveedores; no consumen cuota.
- Antes de dar algo por terminado, ejecuta tests y linter y muestra el resultado.
- Commits pequeños con Conventional Commits.
- Ante una decisión de diseño importante, escribe un ADR corto en docs/adr/.
- Crea .gitignore con .env y results/raw/ antes del primer commit.
