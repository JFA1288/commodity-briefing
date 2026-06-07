"""Claude-powered summarization with SHA-256 content caching.

All functions return None gracefully if ANTHROPIC_API_KEY is absent or call fails.
LLM output is cached in docs/data/llm_cache.json keyed by content hash.
Cache entries older than summarizer.cache_ttl_days are pruned each run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from . import config

_CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "llm_cache.json"

_HAIKU  = "claude-haiku-4-5-20251001"
_SONNET = "claude-sonnet-4-6"


# ── Model config from YAML ────────────────────────────────────────────────────

def _models() -> tuple[str, str]:
    cfg = config.load().get("summarizer", {})
    haiku = cfg.get("model", _HAIKU)
    sonnet = cfg.get("sonnet_model", _SONNET)
    return haiku, sonnet


def _max_tokens() -> int:
    return int(config.load().get("summarizer", {}).get("max_tokens", 300))


def _mode() -> str:
    cfg = config.load()
    # Handle both legacy string "heuristic" and new dict form
    raw = cfg.get("summarizer", "heuristic")
    if isinstance(raw, dict):
        return raw.get("mode", "heuristic")
    return str(raw)


# ── LLM cache ────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"  [cache] write error: {exc}")


def _prune_cache(cache: dict) -> dict:
    ttl = int(config.load().get("summarizer", {}).get("cache_ttl_days", 3)
              if isinstance(config.load().get("summarizer"), dict) else 3)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl)).isoformat()
    return {k: v for k, v in cache.items() if v.get("ts", "") >= cutoff}


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _cached_call(prompt: str, model: str, max_tokens: int) -> Optional[str]:
    """Check cache first; call API on miss; store result."""
    key = _content_hash(prompt + model)
    cache = _load_cache()
    cache = _prune_cache(cache)

    if key in cache:
        return cache[key]["text"]

    text = _api_call(prompt, model, max_tokens)
    if text:
        cache[key] = {"text": text, "ts": datetime.now(timezone.utc).isoformat()}
        _save_cache(cache)
    return text


def _api_call(prompt: str, model: str, max_tokens: int) -> Optional[str]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"  [summarize] API error ({model}): {exc}")
        return None


def _extract_json(text: str):
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


def _client_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


# ── Exec summary (Sonnet — 4-6 decision-ready bullets) ───────────────────────

def summarize_exec_summary(
    biggest_movers: list[dict],    # [{name, change_pct}]
    top_flags: list[dict],         # [{company, signal, headline}]
    themes: list[str],             # active theme names
    risks: list[str],              # standing risk texts
) -> list[str]:
    """Returns list of 4-6 executive bullet strings, or empty list on failure."""
    if _mode() == "heuristic" or not _client_available():
        return _heuristic_exec_summary(biggest_movers, top_flags)

    haiku, sonnet = _models()
    movers_str = "; ".join(f"{m['name']} {m['change_pct']:+.1f}%" for m in biggest_movers[:5]) or "No major moves"
    flags_str = "\n".join(f"- {f['company']} [{f['signal']}]: {f['headline']}" for f in top_flags[:6])
    themes_str = ", ".join(themes[:4]) or "None identified"
    risks_str = "\n".join(f"- {r}" for r in risks[:3])

    prompt = (
        "You are a senior commodity markets analyst briefing advisory partners "
        "who advise SEA commodity trading and energy companies.\n\n"
        "Write 4–6 concise executive bullets covering the most important market signals "
        "from today. Think: what does a partner need to know before a 9am call? "
        "Be specific — name companies, commodities, and numbers. No filler. "
        "Each bullet max 25 words.\n\n"
        f"Biggest price movers: {movers_str}\n\n"
        f"Top flagged company events:\n{flags_str}\n\n"
        f"Active themes: {themes_str}\n\n"
        f"Key risks:\n{risks_str}\n\n"
        'Return JSON only: {"bullets": ["...", "..."]}'
    )
    text = _cached_call(prompt, sonnet, 400)
    if text:
        result = _extract_json(text)
        if result and "bullets" in result:
            return [str(b) for b in result["bullets"][:6]]
    return _heuristic_exec_summary(biggest_movers, top_flags)


def _heuristic_exec_summary(biggest_movers: list[dict], top_flags: list[dict]) -> list[str]:
    bullets: list[str] = []
    movers = [m for m in biggest_movers if abs(m.get("change_pct", 0) or 0) >= 1.0]
    if movers:
        top = movers[0]
        sign = "+" if (top.get("change_pct") or 0) > 0 else ""
        bullets.append(f"Price move: {top['name']} {sign}{top.get('change_pct', 0):.1f}% — largest mover today.")
    for f in top_flags[:4]:
        bullets.append(f"{f['company']} [{f['signal']}]: {f['headline'][:80]}")
    return bullets[:6]


# ── Per-company summary (Haiku) ────────────────────────────────────────────────

def summarize_company(name: str, sector: str, headlines: list[str]) -> Optional[str]:
    """1-2 sentence summary for a company card."""
    if _mode() == "heuristic" or not _client_available() or not headlines:
        return None
    haiku, _ = _models()
    hl_str = "\n".join(f"- {h}" for h in headlines[:4])
    prompt = (
        f"Write 1–2 sentences summarising today's news for {name} ({sector} sector). "
        f"Focus on what's significant: any transactions, disruptions, strategy shifts, or risks. "
        f"Be concise and factual. No filler.\n\nHeadlines:\n{hl_str}\n\nWrite only the summary."
    )
    return _cached_call(prompt, haiku, _max_tokens())


# ── Per-commodity outlook narrative (Haiku) ───────────────────────────────────

def summarize_outlook(commodity: str, eia_text: str, wb_text: str, headlines: list[str], price_context: str = "") -> Optional[str]:
    """2-sentence outlook narrative from available data. Falls back to heuristic if LLM refuses."""
    sources = []
    if price_context:
        sources.append(price_context)
    if eia_text:
        sources.append(f"EIA data: {eia_text}")
    if wb_text:
        sources.append(f"World Bank data: {wb_text}")
    if headlines:
        sources.append("Recent headlines: " + "; ".join(headlines[:3]))
    if not sources:
        return _heuristic_outlook(commodity, eia_text, wb_text, headlines, price_context)

    if _mode() == "heuristic" or not _client_available():
        return _heuristic_outlook(commodity, eia_text, wb_text, headlines, price_context)

    haiku, _ = _models()
    has_wb = bool(wb_text)
    has_forecasts = bool(eia_text or wb_text)
    if has_wb:
        instruction = (
            "Lead with the World Bank benchmark price trend as the primary source. "
            "Supplement with live price movement and any relevant news sentiment. "
            "Cite the World Bank figure explicitly."
        )
    elif has_forecasts:
        instruction = "Cite the data figure and its source. Supplement with news sentiment where available."
    else:
        instruction = "No benchmark data available. Summarise the directional market sentiment from headlines and live price data."

    prompt = (
        f"Write 2 concise sentences summarising the current market outlook for {commodity}. "
        f"{instruction} Do not refuse — use whatever data is provided.\n\n"
        + "\n".join(sources)
        + "\n\nWrite only the 2-sentence outlook. Do not start with 'I'."
    )
    result = _cached_call(prompt, haiku, 180)
    # Detect refusal responses and fall back to heuristic
    if not result or result.lower().startswith(("i appreciate", "i'm unable", "i cannot", "i apologize", "i don't")):
        return _heuristic_outlook(commodity, eia_text, wb_text, headlines, price_context)
    return result


def _heuristic_outlook(commodity: str, eia_text: str, wb_text: str, headlines: list[str], price_context: str) -> Optional[str]:
    """Rule-based outlook narrative when LLM is unavailable or refuses."""
    parts: list[str] = []
    if price_context:
        parts.append(price_context + ".")
    if wb_text:
        parts.append(wb_text + ".")
    if eia_text:
        parts.append(eia_text + ".")
    if headlines:
        parts.append(f"Recent market headlines: {headlines[0]}.")
    if not parts:
        return None
    return " ".join(parts[:2])


# ── Conversation starter (Sonnet) ─────────────────────────────────────────────

def summarize_conversation_starter(context_items: list[str]) -> str:
    """Generate one high-value opening question for a client call."""
    if _mode() == "heuristic" or not _client_available():
        return _heuristic_conversation_starter(context_items)
    haiku, sonnet = _models()
    context_str = "\n".join(f"- {c}" for c in context_items[:3])
    prompt = (
        "You are a Deloitte partner advising a commodity trading company. "
        "Based on today's most significant market development below, write ONE sharp "
        "opening question you would ask a client to start a strategic conversation. "
        "The question should be specific, provoke strategic thinking, and reference "
        "the actual event. Max 30 words.\n\n"
        f"Today's key developments:\n{context_str}\n\n"
        "Write only the question, no preamble."
    )
    result = _cached_call(prompt, sonnet, 80)
    return result or _heuristic_conversation_starter(context_items)


def _heuristic_conversation_starter(context_items: list[str]) -> str:
    if not context_items:
        return ""
    top = context_items[0]
    if "M&A" in top:
        company = top.split("—")[0].replace("M&A:", "").strip()
        return f"With the recent M&A activity at {company}, how is your integration risk framework positioned?"
    if "Price" in top:
        return f"Given today's price moves ({top.replace('Price: ', '')}), how are your hedge book assumptions holding up?"
    return f"With {top.split('—')[0].strip()} in focus today, what's your current exposure and how are you positioned?"


# ── Fundamentals balance read (Haiku) ─────────────────────────────────────────

def summarize_balance_read(commodity: str, balance_read: str, supply_signals: list[str], demand_signals: list[str]) -> Optional[str]:
    """1-sentence balance narrative from EIA + news signals."""
    if _mode() == "heuristic" or not _client_available() or not balance_read:
        return None
    haiku, _ = _models()
    supply_str = ", ".join(supply_signals) if supply_signals else "none flagged"
    demand_str = ", ".join(demand_signals) if demand_signals else "none flagged"
    prompt = (
        f"Write ONE sentence summarising the supply/demand balance for {commodity}. "
        f"Use only the data below — do not invent any figures.\n\n"
        f"Inventory/production: {balance_read}\n"
        f"Supply-side signals: {supply_str}\n"
        f"Demand-side signals: {demand_str}\n\n"
        f"Write only the single sentence."
    )
    return _cached_call(prompt, haiku, 120)


# ── Theme narrative (Haiku) ───────────────────────────────────────────────────

def summarize_theme(theme_name: str, headlines: list[str], companies: list[str], commodities: list[str]) -> Optional[str]:
    """1-paragraph theme narrative."""
    if _mode() == "heuristic" or not _client_available() or not headlines:
        return None
    haiku, _ = _models()
    hl_str = "\n".join(f"- {h}" for h in headlines[:5])
    co_str = ", ".join(companies[:5]) or "various"
    comm_str = ", ".join(commodities[:4]) or "various"
    prompt = (
        f"Write one concise paragraph (3–4 sentences) synthesising the '{theme_name}' theme "
        f"across SEA commodity markets. Mention specific companies and commodities. "
        f"Be factual — only reference signals visible in the headlines provided.\n\n"
        f"Companies involved: {co_str}\nCommodities: {comm_str}\n"
        f"Key headlines:\n{hl_str}\n\nWrite only the paragraph."
    )
    return _cached_call(prompt, haiku, 200)


# ── Risks to watch (Sonnet) ───────────────────────────────────────────────────

def summarize_risks(standing_risks: list[dict], triggered_risks: list[str]) -> Optional[list[dict]]:
    """Returns list of {direction, text} risk bullets, or None on failure."""
    if _mode() == "heuristic" or not _client_available():
        return None
    haiku, sonnet = _models()
    standing_str = "\n".join(f"- [{r['direction']}] {r['text']}" for r in standing_risks[:5])
    triggered_str = "\n".join(f"- {r}" for r in triggered_risks[:4]) if triggered_risks else "None"
    prompt = (
        "You are a commodity markets risk analyst. Write 4–6 balanced risk bullets "
        "(mix of upside and downside) for SEA commodity markets today.\n\n"
        f"Standing risks to incorporate:\n{standing_str}\n\n"
        f"Today's triggered risks from news:\n{triggered_str}\n\n"
        'Return JSON only: {"risks": [{"direction": "upside|downside", "text": "..."}, ...]}'
    )
    text = _cached_call(prompt, sonnet, 300)
    if text:
        result = _extract_json(text)
        if result and "risks" in result:
            return [{"direction": r.get("direction", "downside"), "text": r.get("text", "")}
                    for r in result["risks"][:6]]
    return None


# ── Sector narrative (Haiku) ──────────────────────────────────────────────────

def summarize_sector(
    sector_label: str,
    company_signals: list[dict],
    quiet_companies: list[str],
) -> Optional[str]:
    if _mode() == "heuristic" or not _client_available():
        return None
    haiku, _ = _models()
    lines = "\n".join(
        f"- {cs['company']} [{cs['signal']}]: {cs['headline']}"
        for cs in company_signals
    )
    quiet_str = ", ".join(quiet_companies) if quiet_companies else "None"
    prompt = (
        f"You are a commodity markets analyst briefing a consulting team "
        f"serving SEA commodity trading and energy companies.\n\n"
        f"Write a 2–3 sentence paragraph summarising today's news signals for the "
        f"{sector_label} sector. Focus on what these signals mean operationally. "
        f"Name the specific companies. Be concise — no filler.\n\n"
        f"Companies with signals today:\n{lines}\n\n"
        f"Companies with no material news today: {quiet_str}\n\n"
        f"Write only the summary paragraph."
    )
    return _cached_call(prompt, haiku, 220)


# ── Executive brief (Sonnet) ─────────────────────────────────────────────────

def summarize_executive_brief(
    sector_narratives: list[dict],
    opportunities: list[dict],
) -> Optional[tuple[str, list[str]]]:
    if _mode() == "heuristic" or not _client_available():
        return None
    haiku, sonnet = _models()
    sector_lines = "\n".join(
        f"- {s['label']} ({s['active_count']} active): {s['narrative']}"
        for s in sector_narratives
    )
    opp_lines = "\n".join(
        f"- {o['company']} [{o['signal']}]: {o['headline']}"
        for o in opportunities[:6]
    )
    prompt = (
        "You are a senior commodity markets analyst briefing advisory partners "
        "who advise SEA commodity trading and energy companies.\n\n"
        "Based on today's sector intelligence, produce:\n"
        "1. A 4–5 sentence executive brief covering the most important cross-sector "
        "signals. Be specific — name companies and signal types.\n"
        "2. Three to five short cross-sector theme tags (2–5 words each).\n\n"
        f"Sector intelligence today:\n{sector_lines}\n\n"
        f"Top signals:\n{opp_lines}\n\n"
        'Return JSON only: {"narrative": "...", "themes": ["...", "..."]}'
    )
    text = _cached_call(prompt, sonnet, 450)
    if text:
        result = _extract_json(text)
        if result and "narrative" in result:
            return result["narrative"], result.get("themes", [])
    return None


# ── Opportunity engagement context (Haiku) ────────────────────────────────────

def enrich_opportunities(opportunities: list[dict]) -> Optional[list[str]]:
    if _mode() == "heuristic" or not _client_available() or not opportunities:
        return None
    haiku, _ = _models()
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
    text = _cached_call(prompt, haiku, 300)
    if text:
        result = _extract_json(text)
        if isinstance(result, list):
            return [str(r) for r in result[:len(opportunities)]]
    return None


# ── Company highlights (Haiku) ────────────────────────────────────────────────

def summarize_company_highlights(companies: list[dict]) -> Optional[list[str]]:
    if _mode() == "heuristic" or not _client_available() or not companies:
        return None
    haiku, _ = _models()
    lines = []
    for c in companies:
        hl = "; ".join(c["headlines"][:3])
        lines.append(f"- {c['name']} ({c['sector']}) [{c['signal']}]: {hl}")
    prompt = (
        "For each company below, write 1–2 sentences explaining the strategic context "
        "and what's significant today. Be specific. Name the signal type.\n\n"
        f"Companies:\n" + "\n".join(lines) + "\n\n"
        "Return a JSON array of strings, one per company in order."
    )
    text = _cached_call(prompt, haiku, 400)
    if text:
        result = _extract_json(text)
        if isinstance(result, list):
            return [str(r) for r in result[:len(companies)]]
    return None
