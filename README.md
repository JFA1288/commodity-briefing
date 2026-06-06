# SEA Commodity Briefing

A daily automated digest of commodity market news and prices for Southeast Asia — covering energy, metals, mining, and power sectors. Fully static dashboard published to GitHub Pages. **No paid APIs. No LLM required.**

## Live Dashboard

> `https://<your-github-username>.github.io/commodity-briefing/`

---

## Features

| Feature | Detail |
|---|---|
| **Company news** | 18 companies tracked (Shell, ExxonMobil, BHP, PETRONAS, TNB, PTT, Pertamina …) |
| **Commodity prices** | ~18 commodities: Brent, WTI, JKM LNG, Newcastle coal, copper, nickel, palm oil, iron ore … |
| **Summarization** | Rule-based / extractive — zero LLM API, zero cost |
| **Material-event flags** | Earnings, M&A, outages, regulatory, geopolitical, price shocks |
| **"What to Watch"** | Auto-generated from biggest price movers + flagged events |
| **Dashboard** | Static HTML/JS (Chart.js), light + dark mode, sparklines, collapsible panels |
| **Auto-publish** | GitHub Actions runs daily at 07:00 SGT, commits `docs/` back to `main` |

---

## One-time Setup

1. **Push the repo** to GitHub.
2. **GitHub Pages**: Settings → Pages → Source: *Deploy from a branch* → `main` / `/docs` → Save.
3. **Workflow permissions**: Settings → Actions → General → Workflow permissions → *Read and write permissions* → Save.
4. **First run**: Actions tab → *Daily Commodity Digest* → *Run workflow* → watch it complete and commit.
5. **Verify**: visit `https://<username>.github.io/<repo>/` — the dashboard should be live.

> **Note:** GitHub disables scheduled workflows after ~60 days of repo inactivity. Use *Run workflow* manually as a backup, or push any commit to reset the inactivity timer.

---

## Local Usage

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the agent (fetches news + prices, writes docs/)
python -m src.main

# Dry-run (fetch only, no file writes)
python -m src.main --dry-run

# Preview the dashboard
cd docs && python -m http.server 8080
# open http://localhost:8080
```

---

## Project Structure

```
commodity-briefing/
  config.yaml              # all tuneable settings — companies, commodities, feeds
  requirements.txt
  src/
    config.py              # config loader
    models.py              # Pydantic data models
    fetch_news.py          # Google News RSS + supplementary feeds
    fetch_prices.py        # yfinance + web fallback
    process.py             # dedup, relevance filter, recency filter
    digest.py              # ranking, material-event flags, "what to watch"
    render.py              # writes docs/data/*.json + docs/digests/<date>.html
    main.py                # orchestrator (--dry-run flag)
    templates/
      digest.html.j2       # per-day digest page template
  docs/                    # GitHub Pages root (committed)
    index.html             # live dashboard (fetches data/*.json via JS)
    data/
      latest.json          # regenerated each run
      price_history.json   # appended each run, capped at 90 days
      archive.json         # index of past digests
    digests/               # per-date static digest pages
  .github/workflows/
    daily.yml              # scheduled CI workflow
  tests/
    test_process.py        # unit tests: dedup, relevance, ranking
```

---

## Configuration (`config.yaml`)

All behaviour is controlled via `config.yaml` — no code changes needed for routine edits:

| Key | Purpose |
|---|---|
| `summarizer` | `heuristic` (default, CI-safe) or `ollama` (local only) |
| `companies` | List of companies with display name, aliases, sector, country |
| `commodities` | List of commodities with yfinance ticker or web-search fallback |
| `supplementary_feeds` | Extra RSS feeds with source-weight multipliers |
| `news.lookback_hours` | How far back to keep articles (default 36h) |
| `material_event_keywords` | Keyword lists for event tagging |
| `price_history_days` | Rolling window cap for `price_history.json` (default 90) |

### Adding a company

```yaml
- name: Sarawak Energy
  aliases: ["Sarawak Energy", "SEB", "Sarawak Energy Berhad"]
  country: MY
  sector: power_utilities
```

### Adding a commodity

```yaml
- id: palm_kernel
  display: "Palm Kernel Oil"
  unit: "USD/t"
  group: agri
  yfinance_ticker: null
  web_search_query: "palm kernel oil price today"
```

---

## Data Sources

| Source | Type | Cost |
|---|---|---|
| Google News RSS | Per-company + per-commodity search | Free |
| Reuters, OilPrice.com, Rigzone, Mining.com … | Supplementary RSS | Free |
| yfinance | Brent, WTI, Henry Hub, Copper, Nickel, Aluminium, Soybeans, Wheat … | Free |
| Web search (DuckDuckGo HTML) | Indicative prices for JKM, Tapis, Newcastle coal, iron ore … | Free |

Prices sourced from web fallback are labelled **"web"** (indicative — verify with a live feed before trading decisions). Unavailable prices are labelled **"unavailable"**.

---

## Optional: Local LLM Summaries (Ollama)

The agent supports narrative summaries via a locally running [Ollama](https://ollama.ai) instance. This is **off by default** and **does not work in GitHub Actions** (no GPU/Ollama process in CI).

```yaml
# config.yaml
summarizer: ollama
ollama:
  base_url: http://localhost:11434
  model: llama3.1
```

To use locally:
```bash
ollama serve
ollama pull llama3.1
python -m src.main
```

The default `heuristic` mode requires no external services and runs fine in CI.

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## License

MIT
