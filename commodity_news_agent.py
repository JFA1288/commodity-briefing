"""
Commodity Trading News Agent (v2 — fresh-data fix)
Pulls daily market news via LIVE web search, summarizes with Claude, saves to file.

What changed vs v1:
  * Tells Claude today's real date, so "current" actually means today.
  * Forces recent-only data (last 1-2 days) and asks it to date each figure.
  * Does the search + the summary in ONE connected step, so the write-up is
    grounded directly in the live results (no lossy hand-off).
  * Prints how many live web searches actually ran, so you can confirm it
    is pulling fresh data and not falling back on memory.

Usage:
    python commodity_news_agent.py                     # run once
    python commodity_news_agent.py --schedule daily    # run daily at 7am (blocking)
    python commodity_news_agent.py --output ./reports  # custom output folder
"""

import anthropic
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

# Topics to cover. Edit this list to change what gets searched.
SEARCH_QUERIES = [
    "commodity markets news today prices",
    "oil gas energy trading news today",
    "metals gold silver copper prices today",
    "agricultural commodities wheat corn soybeans news today",
    "commodity market outlook price drivers this week",
]

SYSTEM_PROMPT = """You are a professional commodity markets analyst writing a daily briefing.

Rules you must follow:
1. Use ONLY information from the live web search results in this conversation.
2. Every price or figure you report must be the most recent available. State the
   date or recency of each figure (e.g. "as of <date>" or "in <day>'s trading").
3. If you cannot find current (last 1-2 days) data for a commodity, say so plainly
   instead of using an older number. Never present a stale figure as today's.
4. Note the supply/demand, geopolitical, weather, or macro drivers behind moves.

Format each section as:
- A brief headline in bold (using **)
- 2-3 sentences of analysis
- A one-line "Watch for:" note

Be factual, concise, and actionable."""

# ── Agent ─────────────────────────────────────────────────────────────────────

def build_report(client: anthropic.Anthropic, date_str: str, today_human: str) -> str:
    """Search the live web and produce the briefing in a single grounded call."""
    print("  Searching the live web and writing the briefing...")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        # Stable, widely-supported web search tool version.
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},  # cache the instructions to save cost
        }],
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today_human}. Search the web RIGHT NOW for the very "
                f"latest commodity trading news, published within the last 1-2 days. "
                f"Run a separate search for each of these topics:\n"
                + "\n".join(f"- {q}" for q in SEARCH_QUERIES)
                + "\n\nRead the most recent results, then write today's commodity market "
                "briefing using only that fresh information. Report specific current prices "
                "and percentage moves, and state the date of each figure. If current data "
                "for something isn't available, say so rather than guessing."
            ),
        }],
    )

    # Extract the written briefing AND count how many live searches actually ran.
    summary = ""
    search_count = 0
    for block in response.content:
        btype = getattr(block, "type", "")
        if btype == "text":
            summary += block.text
        elif btype == "server_tool_use" or "search" in btype:
            search_count += 1

    # Fallback: also check the usage object for the search count.
    try:
        stu = getattr(response.usage, "server_tool_use", None)
        if stu is not None:
            reqs = getattr(stu, "web_search_requests", 0) or 0
            search_count = max(search_count, reqs)
    except Exception:
        pass

    print(f"  Live web searches performed: {search_count}")
    if search_count == 0:
        print("  ⚠  WARNING: No live searches ran — the data below may be stale.")
        print("     (Check that web search is enabled for your API key in the Console.)")

    return summary


def save_report(summary: str, output_dir: Path, date_str: str) -> Path:
    """Save the report as a markdown file and append to a JSON log."""
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"commodity_news_{date_str}.md"
    md_content = f"# Commodity Market Report — {date_str}\n\n{summary}\n"
    md_path.write_text(md_content, encoding="utf-8")

    log_path = output_dir / "commodity_news_log.jsonl"
    entry = {
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "report_file": md_path.name,
        "summary": summary,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return md_path


def run_agent(output_dir: Path) -> None:
    """Execute one full news-gathering and summarization cycle."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set.\n"
            'Set it with: setx ANTHROPIC_API_KEY "sk-ant-..."  (then open a new window)'
        )

    client = anthropic.Anthropic(api_key=api_key)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    today_human = now.strftime("%A, %B %d, %Y")

    print(f"\n{'='*60}")
    print(f"  Commodity News Agent — {today_human}")
    print(f"{'='*60}")

    summary = build_report(client, date_str, today_human)
    report_path = save_report(summary, output_dir, date_str)

    print(f"\n  Report saved → {report_path}")
    print(f"\n{'-'*60}")
    print(summary[:600] + ("..." if len(summary) > 600 else ""))
    print(f"{'-'*60}\n")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def schedule_daily(output_dir: Path, hour: int = 7, minute: int = 0) -> None:
    """Block and run the agent once per day at the specified local time."""
    print(f"Scheduler started — will run daily at {hour:02d}:{minute:02d} local time.")
    print("Press Ctrl+C to stop.\n")

    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=target.day + 1)

        wait_seconds = (target - now).total_seconds()
        print(f"  Next run at {target.strftime('%Y-%m-%d %H:%M')} "
              f"({wait_seconds/3600:.1f} hours from now)")

        try:
            time.sleep(wait_seconds)
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            return

        try:
            run_agent(output_dir)
        except Exception as exc:
            print(f"  Error during run: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily commodity trading news agent powered by Claude."
    )
    parser.add_argument("--schedule", choices=["daily"],
                        help="Run on a schedule (daily at 7am local time).")
    parser.add_argument("--hour", type=int, default=7,
                        help="Hour (0-23) for daily schedule (default: 7).")
    parser.add_argument("--output", type=str, default="./commodity_reports",
                        help="Directory to save reports (default: ./commodity_reports).")
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()

    if args.schedule == "daily":
        schedule_daily(output_dir, hour=args.hour)
    else:
        run_agent(output_dir)


if __name__ == "__main__":
    main()
