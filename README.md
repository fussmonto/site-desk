# Site Desk: daily Planning / Billing / QS job board

Zero-cost automation: GitHub Actions fetches jobs every day at 12:00 PM IST, scores them against your CV skills, updates `jobs.json` (which powers the GitHub Pages site) and emails you an HTML digest.

## Files

```
.
├── index.html / style.css / app.js   website (GitHub Pages)
├── config.json                        locations, keywords, salary floors, skills, reputed companies
├── jobs.json                          daily data (written by the workflow)
├── scraper.py                         fetch + filter + score
├── send_email.py                      Gmail digest
├── requirements.txt
└── .github/workflows/daily_job_update.yml
```

## One-time setup

1. **Create the repo.** github.com → New repository → name it e.g. `site-desk` → Public (free GitHub Pages needs public on a free account). Upload all files, keeping the `.github/workflows/` folder path exactly.
2. **Adzuna key (free).** developer.adzuna.com → register → create an app → note **Application ID** and **Application Key**.
3. **JSearch key (free plan).** rapidapi.com → search "JSearch" → Subscribe to the Basic (free) plan → copy your `X-RapidAPI-Key`. The free quota is small, so `config.json` limits it to 5 requests/day. Check the current quota and lower `requests_per_run` if needed.
4. **Gmail App Password.** myaccount.google.com → Security → turn on 2-Step Verification → search "App passwords" → create one named `site-desk` → copy the 16 characters.
5. **Add secrets.** Repo → Settings → Secrets and variables → Actions → New repository secret. Add:

   | Name | Value |
   |---|---|
   | `ADZUNA_APP_ID` | from step 2 |
   | `ADZUNA_APP_KEY` | from step 2 |
   | `RAPIDAPI_KEY` | from step 3 |
   | `GMAIL_USER` | your Gmail address (sender) |
   | `GMAIL_APP_PASSWORD` | 16-character App Password |
   | `EMAIL_TO` | address that should receive the digest (comma-separate for several) |

   Never paste these into chat, a file, or the repo.
6. **Enable Pages.** Settings → Pages → Source: *Deploy from a branch* → Branch `main`, folder `/ (root)` → Save. Your site will be `https://<username>.github.io/<repo>/`.
7. **First run.** Actions tab → *Daily Job Update* → *Run workflow*. Open the log: it prints how many listings were fetched and why others were dropped. Then check your inbox and the site.

After that it runs on its own at 06:30 UTC (12:00 PM IST). GitHub can start scheduled runs a few minutes late. If a repo is inactive for a long time GitHub may pause scheduled workflows; if that happens, re-enable it in the Actions tab.

## Preview locally (optional)

```
pip install -r requirements.txt
python scraper.py --demo          # sample data, no keys needed
python send_email.py --preview    # writes email_preview.html
python -m http.server 8000        # open http://localhost:8000
```
Do not commit demo data; the next real run overwrites it anyway.

## Customising

- **Locations / priority / salary floors:** edit `locations` in `config.json` (`"enabled": false` hides one).
- **Skills and weights:** `skills`. **Reputed employers:** `reputed_companies`.
- **Stricter or looser matches:** `filters.min_score` (default 45).
- **Look and feel:** the variables at the top of `style.css` (colours, fonts, radius).
- **Email size:** `email.max_jobs` (0 = unlimited).

## Honest limitations

- Adzuna gives a shortened job description, so skill matching on those jobs is weaker than on JSearch.
- JSearch runs a rotating subset each day (to stay in the free quota), so Gulf and non-Kolkata JSearch results can be up to a week old.
- Gulf salary data is often missing; salary floors and "typical" bands in `config.json` are my rough estimates, not live market data. Review and adjust them.
- Listings can close before they expire from the dashboard (kept 10 days). Always verify on the employer's site.
