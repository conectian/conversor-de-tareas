"""
conversor-de-tareas — CLI entry point.

Usage:
    python main.py proposal.pdf

Environment variables (or .env file):
    ANTHROPIC_API_KEY   Claude API key (optional — falls back to Claude CLI if not set)
    GITHUB_TOKEN        GitHub personal access token (needs repo + project scopes)
    GITHUB_ORG          GitHub organisation (default: conectian)
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.analyzer import PDFAnalyzer
from src.config import (
    BASE_LABELS, MODULE_HEX, MODULE_PROJECT_COLORS,
    BLOCKER_DEADLINE_DAYS, DEV_DEADLINE_WEEKS,
    STATUS,
)
from src.github_client import GitHubClient
from src.issue_builder import (
    blocker_issue_body, dev_task_issue_body,
    module_label_name, module_label_color, module_label_description,
    blocker_labels, dev_task_labels,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"  ERROR: environment variable {name} is not set.")
        sys.exit(1)
    return val


def _find_field(fields: list, name: str) -> dict | None:
    for f in fields:
        if f.get("name") == name:
            return f
    return None


def _find_option(field: dict, name: str) -> str | None:
    for opt in field.get("options", []):
        if opt["name"] == name:
            return opt["id"]
    return None


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9-]", "-", text.lower().strip())[:50].rstrip("-")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Convert a PDF proposal into a full GitHub project board."
    )
    parser.add_argument("pdf", help="Path to the PDF proposal")
    parser.add_argument(
        "--org", default=None,
        help="GitHub organisation (overrides GITHUB_ORG env var)"
    )
    parser.add_argument(
        "--repo", default=None,
        help="Existing repo name (skips repo creation)"
    )
    parser.add_argument(
        "--project-id", default=None,
        help="Existing GitHub Projects v2 node ID (skips project creation)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse PDF and print plan without creating anything on GitHub"
    )
    args = parser.parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    github_token  = _require_env("GITHUB_TOKEN")
    org = args.org or _require_env("GITHUB_ORG")

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"  ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    # ── 1. Analyze PDF ────────────────────────────────────────────────────────
    print(f"\n📄 Analyzing {pdf_path.name}...")
    analyzer = PDFAnalyzer(api_key=anthropic_key or None)
    project_data = analyzer.analyze(str(pdf_path))

    print(f"\n✅ Extracted project: {project_data.project_name} ({project_data.client_name})")
    print(f"   Ref: {project_data.ref}  |  Modules: {len(project_data.modules)}")
    for m in project_data.modules:
        print(f"   M{m.number}: {m.name}  ({len(m.client_blockers)} blockers, {len(m.dev_tasks)} tasks)")

    if args.dry_run:
        print("\n[dry-run] Stopping before any GitHub API calls.")
        return

    # ── 2. GitHub client ──────────────────────────────────────────────────────
    gh = GitHubClient(token=github_token, org=org)

    # ── 3. Repository ─────────────────────────────────────────────────────────
    client_slug = _slug(project_data.client_name)
    repo_name = args.repo or f"{client_slug}-specs"

    if gh.repo_exists(repo_name):
        print(f"\n📁 Repo {org}/{repo_name} already exists — reusing.")
    else:
        print(f"\n📁 Creating repo {org}/{repo_name}...")
        gh.create_repo(
            name=repo_name,
            description=f"{project_data.project_name} — {project_data.ref}",
        )
        print(f"   ✓ Created.")

    # ── 4. GitHub project board ───────────────────────────────────────────────
    if args.project_id:
        project_id = args.project_id
        print(f"\n📋 Using existing project: {project_id}")
    else:
        board_title = f"{project_data.client_name.lower()} soluciones" \
            if "soluciones" not in project_data.client_name.lower() \
            else project_data.client_name.lower()
        print(f"\n📋 Creating project board: {board_title}...")
        proj = gh.create_project(title=board_title)
        project_id = proj["id"]
        print(f"   ✓ Created: {proj['url']}")

    # ── 5. Labels ─────────────────────────────────────────────────────────────
    print(f"\n🏷  Creating labels in {repo_name}...")
    for lbl in BASE_LABELS:
        gh.create_label(repo_name, lbl["name"], lbl["color"],
                        lbl.get("description", ""))

    for module in project_data.modules:
        gh.create_label(
            repo_name,
            name=module_label_name(module),
            color=module_label_color(module),
            description=module_label_description(module),
        )
    print("   ✓ Done.")

    # ── 6. Project custom fields ──────────────────────────────────────────────
    print(f"\n⚙️  Configuring project fields...")
    fields = gh.get_project_fields(project_id)

    # Find or create Status, Priority, Size fields (GitHub creates these by default)
    status_field  = _find_field(fields, "Status")
    priority_field = _find_field(fields, "Priority")

    # Create Módulo single-select field
    module_field = _find_field(fields, "Módulo")
    if not module_field:
        module_options = [
            {
                "name": f"M{m.number} · {m.name}",
                "color": MODULE_PROJECT_COLORS.get(m.color, "BLUE"),
                "description": m.department or m.description[:60],
            }
            for m in project_data.modules
        ]
        module_field = gh.create_single_select_field(
            project_id, "Módulo", module_options
        )
        print("   ✓ Módulo field created.")
    else:
        print("   ✓ Módulo field already exists.")

    # Map module number → option id
    module_option_ids = {}
    for m in project_data.modules:
        opt_name = f"M{m.number} · {m.name}"
        opt_id = _find_option(module_field, opt_name)
        if opt_id:
            module_option_ids[m.number] = opt_id

    # Priority options
    p0_id = _find_option(priority_field, "P0") if priority_field else None
    p1_id = _find_option(priority_field, "P1") if priority_field else None
    p2_id = _find_option(priority_field, "P2") if priority_field else None

    # Status options
    ready_id    = _find_option(status_field, "Ready")    if status_field else None
    backlog_id  = _find_option(status_field, "Backlog")  if status_field else None

    # ── 7. Issues ─────────────────────────────────────────────────────────────
    print(f"\n📝 Creating issues...")

    for module in project_data.modules:
        print(f"\n   ── M{module.number}: {module.name} ──")

        # Client blockers → Ready, P0
        for blocker in module.client_blockers:
            urgency_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
                blocker.urgency, "🟡"
            )
            title = f"[CLIENTE-M{module.number}] {urgency_icon} {blocker.title}"
            body  = blocker_issue_body(blocker, module, project_data)
            lbls  = blocker_labels(module)

            issue = gh.create_issue(repo_name, title, body, lbls)
            print(f"   ✓ Blocker: {title[:70]}")

            item_id = gh.add_item_to_project(project_id, issue["html_url"])

            # Status → Ready
            if status_field and ready_id:
                gh.set_single_select_field(
                    project_id, item_id, status_field["id"], ready_id
                )
            # Priority → P0
            if priority_field and p0_id:
                gh.set_single_select_field(
                    project_id, item_id, priority_field["id"], p0_id
                )
            # Módulo field
            mod_opt = module_option_ids.get(module.number)
            if module_field and mod_opt:
                gh.set_single_select_field(
                    project_id, item_id, module_field["id"], mod_opt
                )
            # Due date
            due = GitHubClient.deadline_date(days=blocker.deadline_days)
            _set_due_date_if_exists(gh, project_id, item_id, fields, due)

        # Dev tasks → Backlog, P1/P2
        for task in module.dev_tasks:
            title = f"[M{module.number}] {task.title}"
            body  = dev_task_issue_body(task, module, project_data)
            lbls  = dev_task_labels(task, module)

            issue = gh.create_issue(repo_name, title, body, lbls)
            print(f"   ✓ Task:    {title[:70]}")

            item_id = gh.add_item_to_project(project_id, issue["html_url"])

            # Status → Backlog
            if status_field and backlog_id:
                gh.set_single_select_field(
                    project_id, item_id, status_field["id"], backlog_id
                )
            # Priority
            priority_id = p1_id if task.priority == "P1" else p2_id
            if priority_field and priority_id:
                gh.set_single_select_field(
                    project_id, item_id, priority_field["id"], priority_id
                )
            # Módulo field
            mod_opt = module_option_ids.get(module.number)
            if module_field and mod_opt:
                gh.set_single_select_field(
                    project_id, item_id, module_field["id"], mod_opt
                )
            # Due date
            from src.config import DEV_DEADLINE_WEEKS
            weeks = DEV_DEADLINE_WEEKS.get(task.sprint, task.deadline_weeks)
            due = GitHubClient.deadline_date(weeks=weeks)
            _set_due_date_if_exists(gh, project_id, item_id, fields, due)

    print(f"\n✅ All done! Project board ready at GitHub Projects.")
    print(f"   Repo: https://github.com/{org}/{repo_name}")
    print(f"\n💡 Tip: In the project board, go to ⋯ → Fields → enable 'Módulo'")
    print(f"        to see the module tag on every card.")


def _set_due_date_if_exists(gh: GitHubClient, project_id: str,
                             item_id: str, fields: list, date_str: str) -> None:
    for f in fields:
        if f.get("name") in ("Target date", "Due date", "End date", "Fecha límite"):
            try:
                gh.set_date_field(project_id, item_id, f["id"], date_str)
            except Exception:
                pass
            return


if __name__ == "__main__":
    main()
