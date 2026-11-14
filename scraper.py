#!/usr/bin/env python3
"""
scraper.py - Daily job aggregator for Planning / Billing / QS roles.

Run by GitHub Actions every day. Fetches recent jobs from Adzuna and JSearch,
filters and scores them against the CV profile in config.json, merges them with
the jobs already stored in jobs.json, and writes jobs.json for the website and
the email digest.

Usage:
    python scraper.py            # normal run (needs API keys in environment)
    python scraper.py --demo     # write sample data so you can preview the site
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
JOBS_PATH = ROOT / "jobs.json"
IST = timezone(timedelta(hours=5, minutes=30))

ADZUNA_CURRENCY = {"in": "INR", "gb": "GBP", "sg": "SGD", "au": "AUD"}
PERIOD_MULT = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}
OTHER_IN = {"name": "Other India", "group": "India", "priority": 98, "bonus": 1, "country_code": "IN"}
OTHER_ABROAD = {"name": "Other Abroad", "group": "Abroad", "priority": 99, "bonus": 0, "country_code": ""}


class SourceStop(Exception):
    """Raised to stop using one source for this run (quota, bad key...)."""


def log(msg: str) -> None:
    print(msg, flush=True)


def ist_now() -> datetime:
    return datetime.now(IST)


def clean_text(value: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- HTTP
def http_get_json(url: str, params=None, headers=None, tries: int = 3):
    """GET with retries. Never prints the URL (it may contain API keys)."""
    for attempt in range(1, tries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
        except requests.RequestException as exc:
            log(f"    network error ({type(exc).__name__}), attempt {attempt}/{tries}")
            time.sleep(3 * attempt)
            continue
        code = resp.status_code
        if code == 200:
            try:
                return resp.json()
            except ValueError:
                log("    response was not valid JSON")
                return None
        if code == 429:
            raise SourceStop("rate limit or quota reached (HTTP 429)")
        if code in (401, 403):
            raise SourceStop(f"authentication/subscription problem (HTTP {code}) - check the API key secret")
        if code >= 500:
            log(f"    server error HTTP {code}, attempt {attempt}/{tries}")
            time.sleep(3 * attempt)
            continue
        log(f"    HTTP {code} - skipping this query")
        return None
    return None


# ------------------------------------------------------------------------ sources
def fetch_adzuna(cfg: dict, locations: list[dict]) -> list[dict]:
    sc = cfg["sources"]["adzuna"]
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not sc.get("enabled", True):
        return []
    if not (app_id and app_key):
        log("Adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set - skipping.")
        return []

    targets = []
    for l in locations:
        if not (l.get("enabled", True) and l.get("adzuna")):
            continue
        queries = list(sc["queries"])
        if l.get("priority") == 1:
            queries += [q for q in sc.get("priority_extra_queries", []) if q not in queries]
        targets.append((l["adzuna"]["country"], l["adzuna"].get("where", ""), l["adzuna"].get("distance"), queries))
    out: list[dict] = []
    calls = 0
    total = sum(len(t[3]) for t in targets)
    log(f"Adzuna: {len(targets)} places, {total} queries planned (cap {sc['max_requests_per_run']}).")
    try:
        for country, where, distance, queries in targets:
            for query in queries:
                if calls >= sc["max_requests_per_run"]:
                    log("  request cap reached.")
                    return out
                params = {
                    "app_id": app_id, "app_key": app_key,
                    "results_per_page": sc.get("results_per_page", 50),
                    "max_days_old": sc.get("max_days_old", 2),
                    "sort_by": "date", "title_only": query,
                    "content-type": "application/json",
                }
                if where:
                    params["where"] = where
                if distance:
                    params["distance"] = distance
                data = http_get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params)
                calls += 1
                time.sleep(sc.get("delay_seconds", 2.5))
                for r in (data or {}).get("results", []):
                    predicted = str(r.get("salary_is_predicted", "0")) == "1"
                    out.append({
                        "source": "Adzuna",
                        "title": clean_text(r.get("title")),
                        "company": clean_text((r.get("company") or {}).get("display_name")) or "Not disclosed",
                        "location_text": clean_text((r.get("location") or {}).get("display_name")),
                        "cc": country.upper(), "remote": False,
                        "posted": parse_dt(r.get("created")),
                        "url": r.get("redirect_url") or "",
                        "description": clean_text(r.get("description")),
                        "sal_min": None if predicted else r.get("salary_min"),
                        "sal_max": None if predicted else r.get("salary_max"),
                        "sal_currency": ADZUNA_CURRENCY.get(country), "sal_period": "year",
                        "employment": f"{r.get('contract_type') or ''} {r.get('contract_time') or ''}".strip().lower(),
                    })
    except SourceStop as stop:
        log(f"  Adzuna stopped: {stop}")
    log(f"  Adzuna returned {len(out)} raw listings in {calls} requests.")
    return out


def build_jsearch_plan(cfg: dict, locations: list[dict], today) -> list[dict]:
    sc = cfg["sources"]["jsearch"]
    always_names = set(sc.get("always_locations", []))
    always, pool = [], []
    for loc in locations:
        if not loc.get("enabled", True) or not loc.get("jsearch"):
            continue
        for role in sc["role_queries"]:
            item = {"query": f"{role} jobs in {loc['jsearch']}", "country": loc["country_code"].lower(), "remote": False}
            (always if loc["name"] in always_names else pool).append(item)
    for q in sc.get("remote_queries", []):
        pool.append({"query": q, "country": "in", "remote": True})
    budget = sc.get("requests_per_run", 5)
    plan = always[:budget]
    slots = budget - len(plan)
    if slots > 0 and pool:
        start = (today.toordinal() * slots) % len(pool)
        plan += [pool[(start + i) % len(pool)] for i in range(min(slots, len(pool)))]
    return plan


def fetch_jsearch(cfg: dict, locations: list[dict]) -> list[dict]:
    sc = cfg["sources"]["jsearch"]
    key = os.getenv("RAPIDAPI_KEY")
    if not sc.get("enabled", True):
        return []
    if not key:
        log("JSearch: RAPIDAPI_KEY not set - skipping.")
        return []
    plan = build_jsearch_plan(cfg, locations, ist_now().date())
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    out: list[dict] = []
    log(f"JSearch: {len(plan)} requests today.")
    try:
        for item in plan:
            params = {"query": item["query"], "page": "1", "num_pages": "1",
                      "date_posted": sc.get("date_posted", "week"),
                      "employment_types": "FULLTIME", "country": item["country"]}
            if item["remote"]:
                params["work_from_home"] = "true"
            data = http_get_json("https://jsearch.p.rapidapi.com/search", params=params, headers=headers)
            time.sleep(1.5)
            for r in (data or {}).get("data", []) or []:
                loc_text = ", ".join(x for x in [r.get("job_city"), r.get("job_state"), r.get("job_country")] if x)
                out.append({
                    "source": "JSearch",
                    "title": clean_text(r.get("job_title")),
                    "company": clean_text(r.get("employer_name")) or "Not disclosed",
                    "location_text": loc_text,
                    "cc": (r.get("job_country") or "").upper(),
                    "remote": bool(r.get("job_is_remote")),
                    "posted": parse_dt(r.get("job_posted_at_datetime_utc")),
                    "url": r.get("job_apply_link") or "",
                    "description": clean_text(r.get("job_description")),
                    "sal_min": r.get("job_min_salary"), "sal_max": r.get("job_max_salary"),
                    "sal_currency": r.get("job_salary_currency"),
                    "sal_period": (r.get("job_salary_period") or "year").lower(),
                    "employment": (r.get("job_employment_type") or "").lower(),
                })
    except SourceStop as stop:
        log(f"  JSearch stopped: {stop}")
    log(f"  JSearch returned {len(out)} raw listings.")
    return out


# ------------------------------------------------------------------------- engine
def _rx_list(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def _any(rxs: list[re.Pattern], text: str) -> bool:
    return any(r.search(text) for r in rxs)


def _boundary_rx(terms: list[str], case_sensitive: bool = False) -> re.Pattern:
    body = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", 0 if case_sensitive else re.I)


class Engine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        f = cfg["filters"]
        self.f = f
        self.locs = [dict(l) for l in cfg["locations"] if l.get("enabled", True)]
        for loc in self.locs:
            loc["_rx"] = _boundary_rx([a.lower() for a in loc["aliases"]])
        self.include = _rx_list(f["include_title_patterns"])
        self.strict = _rx_list(f["strict_title_patterns"])
        self.context = _rx_list(f["construction_context_patterns"])
        self.exclude = _rx_list(f["exclude_title_patterns"])
        self.block = _rx_list(f["block_title_patterns"])
        self.consult = _rx_list(f["consultancy_company_patterns"])
        self.title_points = [(re.compile(t["pattern"], re.I), t["points"]) for t in cfg["scoring"]["title_points"]]
        self.skills = [(s["name"], s["weight"], _boundary_rx(s["aliases"], s.get("case_sensitive", False)))
                       for s in cfg["skills"]]
        self.gaps = [(g["label"], _boundary_rx(g["aliases"])) for g in cfg.get("gap_keywords", [])]
        self.reputed = _boundary_rx(cfg["reputed_companies"])
        self.exp_range = re.compile(r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
        self.exp_plus = re.compile(r"(?:minimum|min\.?|at least)?\s*(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I)
        self.exp_min = re.compile(r"(?:minimum|min\.?|at least)\s*(\d{1,2})\s*(?:years?|yrs?)", re.I)

    # -- helpers
    def region_for(self, raw: dict) -> dict:
        if raw["remote"]:
            return {**self.cfg["remote"], "priority": 50, "country_code": raw["cc"]}
        text = raw["location_text"].lower()
        for loc in self.locs:
            if loc["_rx"].search(text):
                return loc
        if raw["cc"] == "IN":
            return OTHER_IN
        for loc in self.locs:
            if loc["country_code"] == raw["cc"] and loc["group"] == "Abroad":
                return loc
        return OTHER_ABROAD

    def experience(self, text: str):
        m = self.exp_range.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = self.exp_plus.search(text) or self.exp_min.search(text)
        if m:
            return int(m.group(1)), None
        return None

    @staticmethod
    def level_of(title: str) -> tuple[str, int]:
        t = title.lower()
        if re.search(r"\b(?:assistant|asst\.?|associate|deputy|dy\.?)\s+manager", t):
            return "Asst./Deputy Manager", 10
        if re.search(r"\b(?:senior|sr\.?)\s+manager", t):
            return "Sr. Manager", 7
        if re.search(r"\bmanager\b|\blead\b", t):
            return "Manager", 9
        if re.search(r"\b(?:senior|sr\.?)\b", t):
            return "Senior Engineer", 10
        return "Engineer", 7

    @staticmethod
    def money(cur: str | None, amount: float) -> str:
        return f"{cur or ''} {amount:,.0f}".strip()

    def salary_info(self, raw: dict, loc: dict):
        lo, hi = raw.get("sal_min"), raw.get("sal_max")
        lo = float(lo) if lo else None
        hi = float(hi) if hi else None
        if not (lo or hi):
            return "", "unknown", None
        period = raw.get("sal_period") or "year"
        mult = PERIOD_MULT.get(period, 1)
        lo, hi = lo or hi, hi or lo
        cur = raw.get("sal_currency")
        if cur == "INR" and period == "year":
            text = f"₹{lo/1e5:.1f}-{hi/1e5:.1f} LPA" if lo != hi else f"₹{lo/1e5:.1f} LPA"
        else:
            span = f"{self.money(cur, lo)}-{hi:,.0f}" if lo != hi else self.money(cur, lo)
            text = f"{span} / {period}"
        floor = loc.get("salary_floor")
        flag = "unknown"
        if floor and cur and floor["currency"] == cur:
            annual_best = max(lo, hi) * mult
            tol = self.f.get("salary_tolerance", 0.10)
            flag = "ok" if annual_best >= floor["annual"] * (1 - tol) else "below"
        return text, flag, None

    # -- main evaluation
    def evaluate(self, raw: dict, now_utc: datetime, drops: Counter):
        title, company, desc = raw["title"], raw["company"], raw["description"]
        if not title or not raw["url"]:
            drops["missing title/url"] += 1
            return None
        if _any(self.exclude, title) or not _any(self.include, title):
            drops["title not relevant"] += 1
            return None
        if _any(self.block, title):
            drops["fresher/junior/too senior/contract title"] += 1
            return None
        emp = raw.get("employment", "")
        if re.search(r"contract|part_time|parttime|part-time|intern", emp) and "permanent" not in emp:
            drops["contract/part-time/intern"] += 1
            return None
        blob = f"{title} {company} {desc}"
        if not _any(self.strict, title) and not _any(self.context, blob):
            drops["no construction context"] += 1
            return None
        if raw["posted"] and (now_utc - raw["posted"]).days > self.f["max_age_days"]:
            drops["too old"] += 1
            return None

        exp = self.experience(desc)
        exp_text, exp_pts = "", 3
        p = self.cfg["profile"]
        if exp:
            lo, hi = exp
            if lo > p["accept_required_experience_min_up_to"] or (hi is not None and hi < p["accept_required_experience_max_at_least"]):
                drops["experience mismatch"] += 1
                return None
            exp_text = f"{lo}-{hi} yrs" if hi else f"{lo}+ yrs"
            exp_pts = 5 if lo <= p["experience_years"] <= (hi if hi else lo + 6) else 2

        loc = self.region_for(raw)
        sal_text, sal_flag, _ = self.salary_info(raw, loc)
        if sal_flag == "below" and self.f.get("drop_below_salary_floor", True):
            drops["salary below location floor"] += 1
            return None

        title_pts = self.cfg["scoring"]["title_default_points"]
        for rx, pts in self.title_points:
            if rx.search(title):
                title_pts = pts
                break
        matched, skill_pts = [], 0
        for name, weight, rx in self.skills:
            if rx.search(blob):
                matched.append(name)
                skill_pts += weight
        skill_pts = min(skill_pts, self.cfg["scoring"]["skills_cap"])
        level, level_pts = self.level_of(title)
        reputed = bool(self.reputed.search(company))
        rep_pts = self.cfg["scoring"]["reputed_bonus"] if reputed else 0
        score = max(0, min(100, title_pts + skill_pts + level_pts + exp_pts + rep_pts + loc.get("bonus", 0)))
        if score < self.f["min_score"]:
            drops["below min score"] += 1
            return None

        gaps = [label for label, rx in self.gaps if rx.search(blob)]
        region = loc["name"]
        key_loc = raw["location_text"] if region.startswith("Other") else region
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
        job_id = hashlib.sha1(f"{norm(title)}|{norm(company)}|{norm(key_loc)}".encode()).hexdigest()[:16]
        posted = (raw["posted"] or now_utc).astimezone(IST).strftime("%Y-%m-%d")
        return {
            "id": job_id, "title": title, "company": company,
            "location": raw["location_text"] or region, "region": region, "group": loc["group"],
            "remote": raw["remote"], "type": "Consultancy" if _any(self.consult, company) else "Full-time",
            "level": level, "posted": posted, "url": raw["url"], "source": raw["source"],
            "score": score, "matched_skills": matched, "gaps": gaps, "reputed": reputed,
            "experience": exp_text, "salary_text": sal_text, "salary_flag": sal_flag,
            "market_band": loc.get("market_band", ""),
            "snippet": desc[:240] + ("..." if len(desc) > 240 else ""),
        }


# --------------------------------------------------------------------- persistence
def load_existing() -> dict:
    if JOBS_PATH.exists():
        try:
            return json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        except ValueError:
            log("Existing jobs.json unreadable - starting fresh.")
    return {}


def demo_raw(now_utc: datetime) -> list[dict]:
    def mk(title, company, loc, cc, desc, days=0, remote=False, sal=None):
        return {"source": "Demo", "title": title, "company": company, "location_text": loc, "cc": cc,
                "remote": remote, "posted": now_utc - timedelta(days=days), "url": "#", "description": desc,
                "sal_min": sal[0] if sal else None, "sal_max": sal[1] if sal else None,
                "sal_currency": sal[2] if sal else None, "sal_period": sal[3] if sal else "year", "employment": ""}
    return [
        mk("Sr. Planning Engineer", "Sample Developers Ltd (demo)", "Kolkata, West Bengal", "IN",
           "Construction planning using MS Project, delay analysis, MIS reporting, cost control. 7-12 years. Primavera preferred.", 0, sal=(1100000, 1400000, "INR", "year")),
        mk("Billing Engineer", "Larsen & Toubro (demo)", "Bengaluru, Karnataka", "IN",
           "High-rise residential project. RA bill checking, reconciliation, BBS, BoQ, work orders. 6-10 years.", 1),
        mk("Quantity Surveyor", "Sample QS Consultants (demo)", "Dubai", "AE",
           "Quantity surveying, BoQ, variation orders, subcontractor bill certification for tower project.", 2, sal=(14000, 18000, "AED", "month")),
        mk("Assistant Manager - Planning", "Godrej Properties (demo)", "Pune, Maharashtra", "IN",
           "Residential township scheduling, MS Project, budget vs actual, SAP, MIS. 8-12 years.", 0),
        mk("Senior QS Manager", "Sample Infra (demo)", "Muscat", "OM",
           "Contracts, claims, EOT and cost control for infrastructure project. 10+ years.", 3),
        mk("Planning Engineer (Remote)", "Sample PMC (demo)", "Remote", "IN",
           "Remote planning support for construction projects. MS Project and Excel. 5-9 years.", 1, remote=True),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="write sample data for previewing the site")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    engine = Engine(cfg)
    now = ist_now()
    now_utc = now.astimezone(timezone.utc)
    run_date = now.strftime("%Y-%m-%d")
    existing = {} if args.demo else load_existing()
    old_jobs = {j["id"]: j for j in existing.get("jobs", [])}

    if args.demo:
        raw = demo_raw(now_utc)
    else:
        raw = fetch_adzuna(cfg, engine.locs) + fetch_jsearch(cfg, engine.locs)
        if not raw:
            log("WARNING: no listings fetched. Check API key secrets and the Actions log above.")

    drops: Counter = Counter()
    fresh: dict[str, dict] = {}
    for item in raw:
        job = engine.evaluate(item, now_utc, drops)
        if job and (job["id"] not in fresh or job["score"] > fresh[job["id"]]["score"]):
            fresh[job["id"]] = job

    new_count = 0
    merged = dict(old_jobs)
    for jid, job in fresh.items():
        if jid in old_jobs:
            job["first_seen"] = old_jobs[jid].get("first_seen", run_date)
        else:
            job["first_seen"] = run_date
            new_count += 1
        merged[jid] = job

    cutoff = (now - timedelta(days=cfg["filters"]["retain_days"])).strftime("%Y-%m-%d")
    jobs = [j for j in merged.values() if j.get("first_seen", run_date) >= cutoff]
    jobs.sort(key=lambda j: (-j["score"], j["posted"]), reverse=False)
    jobs.sort(key=lambda j: j["posted"], reverse=True)
    jobs.sort(key=lambda j: j["score"], reverse=True)

    out = {
        "last_updated": now.isoformat(timespec="seconds"),
        "last_updated_display": now.strftime("%d %b %Y, %I:%M %p IST"),
        "run_date": run_date,
        "demo": bool(args.demo),
        "stats": {
            "raw_listings": len(raw), "kept_this_run": len(fresh), "new_today": new_count,
            "total_on_dashboard": len(jobs), "dropped": dict(drops),
        },
        "jobs": jobs,
    }
    tmp = JOBS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(JOBS_PATH)
    log(f"Done: {len(raw)} raw -> {len(fresh)} matched this run, {new_count} new, {len(jobs)} on dashboard.")
    if drops:
        log("Dropped: " + ", ".join(f"{k}={v}" for k, v in drops.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
