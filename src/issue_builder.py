"""
Issue body builder — generates GitHub issue markdown for client blockers and dev tasks.
"""

from datetime import date, timedelta
from typing import List

from .analyzer import ClientBlocker, DevTask, Module, ProjectData
from .config import (
    AWS_SERVICES, AWS_COST_ESTIMATE, TECH_STACK,
    BLOCKER_DEADLINE_DAYS, DEV_DEADLINE_WEEKS,
    MODULE_HEX,
)


# ─── Client blocker issue ─────────────────────────────────────────────────────

URGENCY_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
}

CATEGORY_EMOJI = {
    "credentials": "🔑",
    "data":        "📦",
    "workflow":    "🔄",
    "rules":       "📋",
    "contacts":    "👤",
    "legal":       "⚖️",
}


def blocker_issue_body(blocker: ClientBlocker, module: Module,
                       project: ProjectData) -> str:
    urgency_emoji = URGENCY_EMOJI.get(blocker.urgency, "🟡")
    cat_emoji = CATEGORY_EMOJI.get(blocker.category, "📦")
    deadline = (date.today() + timedelta(days=blocker.deadline_days)).strftime("%d/%m/%Y")

    questions_md = "\n".join(f"- [ ] {q}" for q in blocker.questions)

    return f"""> {urgency_emoji} **Prioridad: P0 — Bloqueante de cliente**
> Módulo: **M{module.number} · {module.name}**
> Categoría: {cat_emoji} `{blocker.category}`
> Fecha límite: **{deadline}** ({blocker.deadline_days} días desde hoy)

---

## ¿Por qué es bloqueante?

{blocker.why_blocking}

## Información que necesitamos del cliente

{questions_md}

---

**Proyecto:** {project.project_name} — {project.client_name}
**Ref:** {project.ref}

> Una vez recibida la información, mover esta card a **Done** y desbloquear las tareas de desarrollo correspondientes.
"""


# ─── Dev task issue ───────────────────────────────────────────────────────────

SIZE_HOURS = {
    "XS": "< 4 h",
    "S":  "4 – 8 h",
    "M":  "8 – 16 h",
    "L":  "16 – 32 h",
    "XL": "> 32 h",
}

TECH_LABELS_STACK = {
    "backend":        f"NestJS (Node.js)",
    "frontend":       f"Angular 17+",
    "ia-ml":          f"FastAPI (Python)",
    "integracion":    f"APIs externas / webhooks",
    "infraestructura": f"AWS (ECS Fargate · RDS Aurora · ElastiCache · S3)",
}


def _tech_stack_section(tech_labels: List[str]) -> str:
    lines = []
    for label in tech_labels:
        stack = TECH_LABELS_STACK.get(label, label)
        lines.append(f"- **{label}**: {stack}")
    return "\n".join(lines)


def _infra_section() -> str:
    rows = "\n".join(f"| `{k}` | {v} |" for k, v in AWS_SERVICES.items())
    return f"""## Arquitectura AWS

| Capa | Servicio |
|------|---------|
{rows}

### Costes estimados
{AWS_COST_ESTIMATE}

### Stack de desarrollo

| Capa | Tecnología |
|------|-----------|
| Frontend | {TECH_STACK['frontend']} → S3 + CloudFront |
| API | {TECH_STACK['backend_api']} → ECS Fargate |
| Servicio IA | {TECH_STACK['ai_service']} → ECS Fargate |
| Base de datos | {TECH_STACK['database']} → RDS Aurora Serverless v2 |
| Caché | {TECH_STACK['cache']} → ElastiCache Redis |
| CI/CD | GitHub Actions → ECR → ECS rolling deploy |
"""


def _acceptance_criteria_md(criteria: List[str]) -> str:
    return "\n".join(f"- [ ] {c}" for c in criteria)


def dev_task_issue_body(task: DevTask, module: Module,
                        project: ProjectData) -> str:
    deadline_weeks = DEV_DEADLINE_WEEKS.get(task.sprint, task.deadline_weeks)
    deadline = (date.today() + timedelta(weeks=deadline_weeks)).strftime("%d/%m/%Y")
    size_label = f"{task.size} ({SIZE_HOURS.get(task.size, '')})"
    sprint_weeks = {1: "1-2", 2: "3-4", 3: "5-6", 4: "7-8"}
    sprint_label = f"Sprint {task.sprint} (semanas {sprint_weeks.get(task.sprint, '?')})"

    tech_section = _tech_stack_section(task.tech_labels)
    criteria_md = _acceptance_criteria_md(task.acceptance_criteria)

    infra_block = ""
    if "infraestructura" in task.tech_labels and task.sprint == 1:
        infra_block = "\n" + _infra_section()

    return f"""> **Módulo:** M{module.number} · {module.name}
> **Prioridad:** {task.priority} | **Tamaño:** {size_label}
> **{sprint_label}** | **Fecha límite:** {deadline}

---

## Descripción

{task.description}

## Stack técnico
{tech_section}
{infra_block}
## Criterios de aceptación

{criteria_md}

---

**Proyecto:** {project.project_name} — {project.client_name}
**Ref:** {project.ref}
"""


# ─── Label / field helpers ────────────────────────────────────────────────────

def module_label_name(module: Module) -> str:
    return f"modulo-{module.number}"


def module_label_color(module: Module) -> str:
    return MODULE_HEX.get(module.color, "1D76DB")


def module_label_description(module: Module) -> str:
    return f"M{module.number} · {module.name}"


def blocker_labels(module: Module) -> List[str]:
    return ["bloqueante", "cliente", "prioridad-1", module_label_name(module)]


def dev_task_labels(task: DevTask, module: Module) -> List[str]:
    labels = list(task.tech_labels)
    if task.priority == "P1":
        labels.append("prioridad-1")
    labels.append(module_label_name(module))
    return labels
