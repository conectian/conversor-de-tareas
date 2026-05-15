"""
PDF analyzer — sends the proposal PDF to Claude and returns structured ProjectData.

Two backends:
  1. Anthropic SDK  — when ANTHROPIC_API_KEY is set (or api_key passed explicitly)
  2. Claude CLI     — falls back to `claude -p` (Claude Code Max OAuth session)
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ─── Data models ─────────────────────────────────────────────────────────────

@dataclass
class ClientBlocker:
    title: str
    category: str          # credentials | data | workflow | rules | contacts
    urgency: str           # critical | high | medium
    deadline_days: int
    questions: List[str]
    why_blocking: str


@dataclass
class DevTask:
    title: str
    description: str
    priority: str          # P1 | P2
    size: str              # XS | S | M | L | XL
    sprint: int
    deadline_weeks: int
    tech_labels: List[str] # backend | frontend | ia-ml | integracion | infraestructura
    acceptance_criteria: List[str]


@dataclass
class Module:
    number: int
    name: str
    department: str
    description: str
    budget: float
    color: str             # BLUE | GREEN | RED | ORANGE | PURPLE | YELLOW
    client_blockers: List[ClientBlocker] = field(default_factory=list)
    dev_tasks: List[DevTask] = field(default_factory=list)


@dataclass
class ProjectData:
    client_name: str
    project_name: str
    ref: str
    total_budget: float
    timeline_months: int
    modules: List[Module] = field(default_factory=list)


# ─── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior software project manager and business analyst.
You analyze technical proposal PDFs and extract structured project data to automate
GitHub project board creation.

You must return ONLY valid JSON — no markdown fences, no explanation, just the JSON object.
"""

ANALYSIS_PROMPT = """Analyze this technical/commercial proposal PDF and extract ALL information needed
to create a professional GitHub project board for the development team.

Return ONLY this JSON structure (no markdown, no extra text):

{
  "client_name": "Exact client company name from the document",
  "project_name": "Project or platform name",
  "ref": "Proposal reference number (e.g. CT-2026-062)",
  "total_budget": 0,
  "timeline_months": 2,
  "modules": [
    {
      "number": 1,
      "name": "Module name",
      "department": "Department name",
      "description": "One sentence description of what this module does",
      "budget": 0,
      "color": "BLUE",
      "client_blockers": [
        {
          "title": "Concise title of what we need from the client",
          "category": "credentials",
          "urgency": "critical",
          "deadline_days": 4,
          "questions": [
            "Specific question 1 we need answered",
            "Specific question 2 we need answered"
          ],
          "why_blocking": "One sentence explaining why development cannot start without this"
        }
      ],
      "dev_tasks": [
        {
          "title": "Development task title",
          "description": "What needs to be built technically",
          "priority": "P1",
          "size": "M",
          "sprint": 1,
          "deadline_weeks": 2,
          "tech_labels": ["backend", "frontend"],
          "acceptance_criteria": [
            "Measurable acceptance criterion 1",
            "Measurable acceptance criterion 2"
          ]
        }
      ]
    }
  ]
}

RULES — follow strictly:

CLIENT BLOCKERS (client_blockers):
- Things we need FROM the client BEFORE development can start
- Include: API credentials, existing data/files, business rules, workflow definitions,
  system access, account creation, legal/compliance requirements
- category options: credentials | data | workflow | rules | contacts | legal
- urgency critical (deadline 3-5 days): API/system access that blocks all development
- urgency high (deadline 5-7 days): Data/credentials that block module start
- urgency medium (deadline 7-10 days): Business rules/workflows needed for design
- Always include 2-5 specific questions per blocker
- Be specific about WHY it blocks (not generic)

DEV TASKS (dev_tasks):
- Technical implementation tasks for the development team
- ALWAYS include as first task of first module: infrastructure setup (AWS: S3+CloudFront
  for Angular, ECS Fargate for NestJS API, ECS Fargate for FastAPI AI service,
  RDS Aurora PostgreSQL Serverless v2, ElastiCache Redis, GitHub Actions CI/CD)
- ALWAYS include auth/roles task if multi-user system
- Group logically: infrastructure → auth → core data → features → integrations → reports
- priority P1: foundational tasks that others depend on
- priority P2: feature tasks built on top of P1
- size: XS(<4h) S(4-8h) M(8-16h) L(16-32h) XL(>32h)
- sprint 1=weeks 1-2, sprint 2=weeks 3-4, sprint 3=weeks 5-6, sprint 4=weeks 7-8
- tech_labels from: backend | frontend | ia-ml | integracion | infraestructura
- Include 2-4 measurable acceptance criteria

MODULE COLORS: first module=BLUE, second=GREEN, third=RED, fourth=ORANGE, fifth=PURPLE

Extract ALL features from the document — be thorough, don't skip functionality.
Create one dev task per logical feature group (not one per bullet point).
"""


# ─── Analyzer class ───────────────────────────────────────────────────────────

class PDFAnalyzer:
    def __init__(self, api_key: str = None, model: str = "claude-sonnet-4-6"):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._use_sdk = bool(self._api_key)

        if self._use_sdk:
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(api_key=self._api_key)
        else:
            self._claude_bin = shutil.which("claude") or self._find_claude_bin()
            if not self._claude_bin:
                raise RuntimeError(
                    "No ANTHROPIC_API_KEY found and `claude` CLI not in PATH. "
                    "Set ANTHROPIC_API_KEY or install Claude Code."
                )
            print(f"  No API key found — using Claude CLI: {self._claude_bin}")

    @staticmethod
    def _find_claude_bin() -> str | None:
        exts = Path.home() / ".vscode" / "extensions"
        if exts.exists():
            for p in sorted(exts.glob("anthropic.claude-code-*/resources/native-binary/claude"), reverse=True):
                if p.exists():
                    return str(p)
        return None

    def analyze(self, pdf_path: str) -> ProjectData:
        if self._use_sdk:
            raw = self._analyze_via_sdk(pdf_path)
        else:
            raw = self._analyze_via_cli(pdf_path)

        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return self._parse(data)

    def _analyze_via_sdk(self, pdf_path: str) -> str:
        pdf_bytes = Path(pdf_path).read_bytes()
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        print(f"  Sending PDF to Claude API ({self.model})...")
        message = self._client.messages.create(
            model=self.model,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": ANALYSIS_PROMPT},
                    ],
                }
            ],
        )
        return message.content[0].text

    def _analyze_via_cli(self, pdf_path: str) -> str:
        # Extract PDF text so the CLI can read it
        pdf_text = self._extract_pdf_text(pdf_path)
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"===PDF CONTENT===\n{pdf_text}\n===END PDF===\n\n"
            f"{ANALYSIS_PROMPT}"
        )

        print(f"  Sending PDF to Claude CLI (Max account)...")
        result = subprocess.run(
            [self._claude_bin, "-p", "--output-format", "text",
             "--dangerously-skip-permissions"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude CLI failed: {result.stderr[:500]}")
        return result.stdout

    @staticmethod
    def _extract_pdf_text(pdf_path: str) -> str:
        # Try pypdf first, then pdftotext binary
        try:
            from pypdf import PdfReader
            reader = PdfReader(pdf_path)
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except ImportError:
            pass

        result = subprocess.run(
            ["pdftotext", pdf_path, "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout

        raise RuntimeError(
            "Cannot extract PDF text: install pypdf (`pip install pypdf`) "
            "or pdftotext (`apt install poppler-utils`)."
        )

    def _parse(self, data: dict) -> ProjectData:
        modules = []
        for m in data.get("modules", []):
            blockers = [
                ClientBlocker(
                    title=b["title"],
                    category=b.get("category", "data"),
                    urgency=b.get("urgency", "high"),
                    deadline_days=int(b.get("deadline_days", 7)),
                    questions=b.get("questions", []),
                    why_blocking=b.get("why_blocking", ""),
                )
                for b in m.get("client_blockers", [])
            ]
            tasks = [
                DevTask(
                    title=t["title"],
                    description=t.get("description", ""),
                    priority=t.get("priority", "P2"),
                    size=t.get("size", "M"),
                    sprint=int(t.get("sprint", 1)),
                    deadline_weeks=int(t.get("deadline_weeks", 4)),
                    tech_labels=t.get("tech_labels", ["backend"]),
                    acceptance_criteria=t.get("acceptance_criteria", []),
                )
                for t in m.get("dev_tasks", [])
            ]
            modules.append(
                Module(
                    number=int(m.get("number", len(modules) + 1)),
                    name=m["name"],
                    department=m.get("department", ""),
                    description=m.get("description", ""),
                    budget=float(m.get("budget", 0)),
                    color=m.get("color", "BLUE"),
                    client_blockers=blockers,
                    dev_tasks=tasks,
                )
            )

        return ProjectData(
            client_name=data["client_name"],
            project_name=data["project_name"],
            ref=data.get("ref", ""),
            total_budget=float(data.get("total_budget", 0)),
            timeline_months=int(data.get("timeline_months", 2)),
            modules=modules,
        )
