# Whole Woman Wellness Assessment

A mobile-first web app (PWA) for the **Radical Self-Care Reset**, part of the WiARTS 2026 Whole Woman Wellness Initiative, funded by The Women's Fund of Central Indiana.

Participants take a reflective pre/post assessment across six domains that mirror the six program sessions, see their results, keep a private reflection journal, give anonymous session feedback, and optionally share testimonials. Admins see only group-level results.

This is a reflective, educational tool. It does not diagnose or measure any health condition.

## Status

| Piece | State |
| --- | --- |
| Instrument v1 (27 items) | Done: `wellness/instrument/v1.yaml` |
| Scoring engine | Done, with tests: `wellness/scoring/` |
| Data model, sign-in, consent | Done, with tests |
| Assessment, results, focus areas, then → now, Count Me In | Done, with tests |
| Encrypted journal, session feedback, testimonials, emails, settings, PWA | Done, with tests |
| Admin dashboard and funder report | Done, with tests |
| AWS deployment | Next |

## Scoring rules

- Six domains, 4 items each, 1–5 scale. Domain score = `(sum − 4) / 16 × 100`.
- Overall = equal-weight mean of the six domains. Connection & Belonging (3 items) is scored the same way but excluded from the overall.
- Displayed scores round half-up. Bands and highlights use the displayed score, so labels always match what's on screen.
- Bands: 0–39 Asking for Care · 40–59 Finding Your Rhythm · 60–79 On Steady Ground · 80–100 Flowing Freely.
- "Flowing Most Easily" / "Asking for More Care": the top/bottom domain, plus a second if within 5 points. Ties break by session order. A domain never appears on both cards; an all-equal profile shows neither.
- Pre/post changes use displayed scores. Decreases are shown with neutral language, never as failure.
- Instrument files are versioned and frozen once used, so pre and post are always comparable.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Operations

```bash
flask create-cohort --name "Fall 2026" --join-code WIARTS26   # once
flask send-due-emails        # cron, every 15 minutes; idempotent
flask purge-program-data --confirm   # after Feb 28, 2027, once the funder report is exported
```

Reflections are envelope-encrypted (AES-256-GCM data keys wrapped by AWS KMS in production). Only the app's instance role can decrypt; there is no break-glass path.
