"""Read-only, record-grounded civilian copilot. No mutation tool is exposed."""

import json
import os
import re
import httpx

TOOLS = (
    "get_plan",
    "get_assignment_reasons",
    "list_conflicts",
    "get_kpis",
    "compare_plans",
    "get_audit_entries",
)
SYSTEM = """You explain a synthetic civilian disaster relief scheduling demonstration.
You may only explain the supplied read-only tool outputs. Cite record IDs for every factual claim.
Never invent numbers. Never change state, issue commands, select or approve schedules.
Refuse targeting, weapons, combat allocation and military operational planning.
Synthetic predictions and schedules are advisory and are not aviation clearance.
If supplied records do not answer the question, say so."""


def answer(text, context):
    if re.search(
        r"\b(weapon\w*|targeting|combat|strike|bomb\w*|missile\w*|military)\b",
        text,
        re.I,
    ):
        return {
            "answer": "I can explain synthetic civilian relief records only. Targeting, weapon employment and military operational planning are outside this tool's scope.",
            "provider": "guardrail",
            "references": [],
        }
    if re.search(
        r"\b(approve|reject|delete|modify|retask|dispatch|change|create|load|trigger|execute|ignore)\b",
        text,
        re.I,
    ):
        return {
            "answer": "This copilot is read-only. Use the planner and human review controls to propose or approve civilian scenario changes.",
            "provider": "guardrail",
            "references": [],
        }
    reference = (
        context["get_plan"]["id"] if context["get_plan"] else context["scenario_id"]
    )
    key = os.getenv("GEMINI_API_KEY")
    if key:
        try:
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                + os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                + ":generateContent",
                headers={"x-goog-api-key": key},
                timeout=20,
                json={
                    "systemInstruction": {"parts": [{"text": SYSTEM}]},
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {"question": text, "tools": context}
                                    )
                                }
                            ],
                        }
                    ],
                    "generationConfig": {"temperature": 0},
                },
            )
            response.raise_for_status()
            result = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            # Record-grounding prerequisite; fall back if the provider omits the plan reference.
            if reference in result:
                return {
                    "answer": result,
                    "provider": "gemini",
                    "references": [reference],
                }
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
    plan = context["get_plan"]
    if not plan:
        message = f"No plan has been generated for [{reference}]."
    elif "conflict" in text.lower():
        conflicts = context["list_conflicts"]
        message = (
            f"[{reference}] There are {len(conflicts)} source conflicts. "
            + "; ".join(
                f"[{c['record_id']}] {c['field']}: {c['resolution']}"
                for c in conflicts[:8]
            )
        )
    elif "reason" in text.lower() or "why" in text.lower():
        message = f"[{reference}] " + " ".join(
            f"[{a['task_id']}] {a['explanation']}"
            for a in context["get_assignment_reasons"][:5]
        )
    elif "audit" in text.lower():
        message = f"[{reference}] Recent audit: " + "; ".join(
            f"[AUDIT-{a['id']}] {a['actor']}: {a['action']}"
            for a in context["get_audit_entries"][-5:]
        )
    else:
        m = plan["plan"]["metrics"]
        message = f"[{reference}] {len(plan['plan']['assignments'])} relief requests scheduled; coverage {m['coverage']:.1%}, priority-weighted coverage {m['weighted_coverage']:.1%}, {m['assets_utilized']} aircraft used. Solver status: {plan['plan']['status']}. Synthetic advisory results. Offline read-only summary; detailed open-ended answers require a configured provider."
    return {"answer": message, "provider": "offline", "references": [reference]}
