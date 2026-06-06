"""Claude-powered summarization for sector narratives, executive brief, opportunities, and company highlights.

All functions return None gracefully if ANTHROPIC_API_KEY is absent or the call fails,
allowing the caller to fall back to rule-based content.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

_HAIKU  = "claude-haiku-4-5-20251001"
_SONNET = "claude-sonnet-4-6"


def _client():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception:
        return None


def _extract_json(text: str):
    """Parse JSON from text that may have surrounding prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


# ── Sector narrative (Haiku) ──────────────────────────────────────────────────

def summarize_sector(
    sector_label: str,
    company_signals: list[dict],
    quiet_companies: list[str],
) -> Optional[str]:
    """2-3 sentence consulting-focused narrative for a sector."""
    client = _client()
    if not client:
        return None
    try:
        lines = "\n".join(
            f"- {cs['company']} [{cs['signal']}]: {cs['headline']}"
            for cs in company_signals
        )
        quiet_str = ", ".join(quiet_companies) if quiet_companies else "None"
        prompt = (
            f"You are a commodity markets analyst briefing a consulting team"
            f"serving SEA commodity trading and energy companies.\n\n"
            f"Write a 2–3 sentence paragraph summarising today's news signals for the "
            f"{sector_label} sector. Focus on what these signals mean as potential "
            f"consulting opportunities. Name the specific companies. Be concise — no filler.\n\n"
            f"Companies with signals today:\n{lines}\n\n"
            f"Companies with no material news today: {quiet_str}\n\n"
            f"Write only the summary paragraph."
        )
        msg = client.messages.create(
            model=_HAIKU,
            max_tokens=220,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"  [summarize] sector narrative error ({sector_label}): {exc}")
        return None


# ── Executive brief (Sonnet) ──────────────────────────────────────────────────

def summarize_executive_brief(
    sector_narratives: list[dict],  # [{label, narrative, active_count}]
    opportunities: list[dict],      # [{company, signal, headline}]
) -> Optional[tuple[str, list[str]]]:
    """Returns (narrative, themes) using Sonnet, or None on failure."""
    client = _client()
    if not client:
        return None
    try:
        sector_lines = "\n".join(
            f"- {s['label']} ({s['active_count']} active): {s['narrative']}"
            for s in sector_narratives
        )
        opp_lines = "\n".join(
            f"- {o['company']} [{o['signal']}]: {o['headline']}"
            for o in opportunities[:6]
        )
        prompt = (
            "You are a senior commodity markets analyst briefing advisory partners"
            "who advise SEA commodity trading and energy companies.\n\n"
            "Based on today's sector intelligence, produce:\n"
            "1. A 4–5 sentence executive brief covering the most important cross-sector "
            "signals and what they mean for consulting mandates. Be specific — name companies "
            "and signal types. Write as if briefing a partner before a client call.\n"
            "2. Three to five short cross-sector theme tags (2–5 words each) capturing "
            "today's dominant patterns.\n\n"
            f"Sector intelligence today:\n{sector_lines}\n\n"
            f"Top consulting opportunities:\n{opp_lines}\n\n"
            'Return JSON only: {"narrative": "...", "themes": ["...", "..."]}'
        )
        msg = client.messages.create(
            model=_SONNET,
            max_tokens=450,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json(msg.content[0].text)
        if result and "narrative" in result:
            return result["narrative"], result.get("themes", [])
    except Exception as exc:
        print(f"  [summarize] executive brief error: {exc}")
    return None


# ── Opportunity engagement context (Haiku) ────────────────────────────────────

def enrich_opportunities(
    opportunities: list[dict],  # [{company, signal, headline}]
) -> Optional[list[str]]:
    """One-line engagement context per opportunity (team · duration · service type)."""
    client = _client()
    if not client or not opportunities:
        return None
    try:
        lines = "\n".join(
            f"{i+1}. {o['company']} [{o['signal']}]: {o['headline']}"
            for i, o in enumerate(opportunities)
        )
        prompt = (
            "For each consulting opportunity below, write ONE short line (max 12 words) "
            "describing the typical advisory engagement."
            'Format: "Typical scope: [team] · [duration] · [service]"\n\n'
            f"Opportunities:\n{lines}\n\n"
            "Return a JSON array of strings, one per opportunity in order."
        )
        msg = client.messages.create(
            model=_HAIKU,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json(msg.content[0].text)
        if isinstance(result, list):
            return [str(r) for r in result[:len(opportunities)]]
    except Exception as exc:
        print(f"  [summarize] opportunity enrich error: {exc}")
    return None


# ── Company highlight insights (Haiku) ────────────────────────────────────────

def summarize_company_highlights(
    companies: list[dict],  # [{name, sector, signal, headlines: [str]}]
) -> Optional[list[str]]:
    """1–2 sentence strategic insight per company."""
    client = _client()
    if not client or not companies:
        return None
    try:
        lines = []
        for c in companies:
            hl = "; ".join(c["headlines"][:3])
            lines.append(f"- {c['name']} ({c['sector']}) [{c['signal']}]: {hl}")
        prompt = (
            "For each company below, write 1–2 sentences explaining the strategic context "
            "and why this matters now for a consulting firm advising them. Be specific about "
            "the consulting opportunity and any urgency.\n\n"
            f"Companies:\n" + "\n".join(lines) + "\n\n"
            "Return a JSON array of strings, one per company in order."
        )
        msg = client.messages.create(
            model=_HAIKU,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _extract_json(msg.content[0].text)
        if isinstance(result, list):
            return [str(r) for r in result[:len(companies)]]
    except Exception as exc:
        print(f"  [summarize] company highlight error: {exc}")
    return None
