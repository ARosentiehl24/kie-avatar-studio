# 0004. Arquitectura por capas con puertos (DIP)

Fecha: 2026-05-31
Estado: Aceptado

## Contexto

Necesitábamos una arquitectura simple, fácil de razonar y testeable, que permitiera:

- Sustituir clientes HTTP y persistencia sin tocar la lógica de orquestación.
- Mantener la TUI desacoplada del transporte real.
- Validar invariantes en un único lugar.
- Soportar concurrencia (paralelismo intra-job y entre jobs) sin pelearse con la UI.

Otras opciones consideradas (Vertical Slice + CQRS + DDD) traían complejidad estructural
desproporcionada para un proyecto local monousuario, con un único bounded context y un equipo
pequeño. Hexagonal "pura" introducía vocabulario que no aporta sobre este tamaño.

## Decisión

Adoptamos una **arquitectura por capas con puertos** y un **composition root** explícito:

```text
ui          → app_layer, domain
app_layer   → domain                 (NUNCA infra)
infra       → domain (solo DTOs)
domain      → nada interno
app.py      → infra, app_layer, ui   (única excepción autorizada)
```

Reglas concretas:

1. `domain/ports.py` declara `Protocol` con `@runtime_checkable`
   (`KieGateway`, `JobRepository`). DIP queda explícito y verificable.
2. `infra/` implementa los `Protocol`. Sin reglas de negocio.
3. `app_layer/` (`JobRunner`, `QueueManager`) depende solo de los `Protocol` del dominio.
4. `ui/` solo habla con `app_layer` y `domain`.
5. `app.py` es el único módulo que conoce las clases concretas de `infra/` y las inyecta.

Errores tipados (`KieError` y subclases, `JobValidationError`) viven en `domain/errors.py`.
Validación en `domain/policies.py`. Constantes nombradas (timeouts, límites de Kie, backoff,
chunk size de descarga) viven en `policies` o en `Settings`; nunca inline.

## Consecuencias

Positivas

- Capas pequeñas con responsabilidad única; SRP trivial de verificar.
- Tests unitarios de `app_layer` no requieren HTTP ni SQLite: se mockean los `Protocol`.
- Cualquier cambio de proveedor (Kie → otro) toca solo `infra/` y un punto en `app.py`.
- `import-linter` puede congelar las reglas y hacerlas fallar en CI si alguien las rompe.

Negativas

- Una capa "domain" parece pesada para un proyecto chico, pero centraliza invariantes.
- Hay que disciplinar el equipo a no atajar imports (de `app_layer` a `infra`, por ejemplo).
  Se mitiga con `import-linter` + agente `code-quality-reviewer`.
- No cubre escenarios multi-bounded-context. Si aparecen, se reevalúa una migración a
  modules/ por bounded context.

## Alternativas consideradas

- **Vertical Slice + CQRS + DDD**: descartado por exceso de estructura para un único
  bounded context y app local. Reabriremos la discusión si crece el dominio.
- **Hexagonal pura**: equivalente conceptual a lo que tenemos; preferimos el vocabulario
  "capas + puertos" porque coincide con el layout físico de carpetas.
- **Sin capas (todo en un paquete plano)**: rápido al inicio, pero el acoplamiento entre
  TUI, HTTP y SQLite se vuelve inmanejable al primer refactor.

## Cumplimiento

- `docs/ARCHITECTURE.md` describe las reglas y el flujo.
- `docs/CODE_QUALITY.md` extiende con clean code/SOLID.
- `.importlinter` codifica los contratos.
- `.opencode/agents/code-quality-reviewer.md` y `.github/agents/code-quality-reviewer.md`
  son el revisor automático que cita estas reglas en cada cambio.
