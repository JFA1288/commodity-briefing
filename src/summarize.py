"""Claude Haiku-powered sector narrative summarizer.

Falls back gracefully to None if ANTHROPIC_API_KEY is not set or the call fails,
allowing the caller to use a rule-based fallback instead.
"""

from __future__ import annotations

import os
from typing import Optional

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 220


def summarize_sector(
    sector_label: str,
    company_signals: list[dict],   # [{'company': str, 'signal': str, 'headline': str}]
    quiet_companies: list[str],
) -> Optional[str]:
    """Return a 2-3 sentence consulting-focused narrative for a sector, or None on failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import anthropic

        lines = "\n".join(
            f"- {cs['company']} [{cs['signal']}]: {cs['headline']}"
            for cs in company_signals
        )
        quiet_str = ", ".join(quiet_companies) if quiet_companies else "None"

        prompt = (
            f"You are a commodity markets analyst briefing a consulting team at a firm like Deloitte "
            f"that serves commodity trading and energy companies.\n\n"
            f"Write a 2–3 sentence paragraph summarising today's news signals for the "
            f"{sector_label} sector. Focus on what these signals mean as potential consulting "
            f"opportunities. Name the specific companies. Be concise and direct — no filler phrases.\n\n"
            f"Companies with signals today:\n{lines}\n\n"
            f"Companies with no material news today: {quiet_str}\n\n"
            f"Write only the summary paragraph."
        )

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()

    except Exception as exc:
        print(f"  [summarize] Claude API error for {sector_label}: {exc}")
        return None
