#!/usr/bin/env python3
"""Hallway Track: listens to buyer conversations, not competitors.

Every run it:
  1. Collects public conversations from Hacker News, Information Security Stack
     Exchange, Mastodon (infosec.exchange), Google News and, if keys are set, Reddit.
  2. Keeps on-topic ones and tags each with buyer persona, pains and buying intent.
  3. Finds communities practitioners mention (Slack, Discord, subreddits, events)
     and ranks the rooms a team should be in, with links to the evidence.
  4. Pulls the phrases buyers repeat, so content and talk tracks use their words.
  5. Uses GitHub Models (free) for a weekly buyer brief and a suggested reply
     for in-market conversations.
  6. Saves everything to hallway.json for the dashboard (index.html).

Privacy: only public posts are read, usernames are never stored, and each
conversation keeps a short excerpt plus a link back to the original.

Run locally:  python hallway.py --dry-run
"""
import base64
import hashlib
import json
import math
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, "hallway.json")
NOW = datetime.now(timezone.utc)
UA = {"User-Agent": "hallway-track/1.0 (public research; +https://github.com)"}
MODEL_URL = "https://models.github.ai/inference/chat/completions"
MODELS = [m for m in [os.environ.get("HALLWAY_MODEL"), "openai/gpt-4.1-mini", "openai/gpt-4o-mini"] if m]
EXCERPT = 280

STOP = set("""a about above after again against all am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how i if in into is it its itself just me more most my
no nor not now of off on once only or other our ours out over own same she should so some such than
that the their them then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours i'm it's don't can't we're
they're you're i've we've isn't doesn't didn't won't also really like get got one two us use used
using any anyone thing things lot much many even still well way make made want need know think see
going go new time year years day days people every something someone etc via re http https www com
gt lt amp quot nbsp""".split())


# ---------------- Helpers ----------------
def load_toml(name):
    with open(os.path.join(ROOT, name), "rb") as f:
        return tomllib.load(f)


def http(url, headers=None, data=None, timeout=25):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        print(f"  {e.code} from {url[:80]}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f"  fetch failed: {url[:80]} ({e})")
    return None


def clean(text):
    text = unescape(unescape(re.sub(r"<[^>]+>", " ", text or "")))
    return re.sub(r"\s+", " ", text).strip()


def hit(text, phrase):
    p = phrase.lower().strip()
    if not p:
        return False
    if len(p) <= 3:
        return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text) is not None
    return re.search(r"(?<![a-z0-9])" + re.escape(p), text) is not None


def day(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def cid(source, key):
    return hashlib.sha1(f"{source}|{key}".encode()).hexdigest()[:12]


# ---------------- Collectors ----------------
def from_hn(queries, since):
    out = []
    for q in queries:
        for tag in ("story", "comment"):
            url = ("https://hn.algolia.com/api/v1/search_by_date?hitsPerPage=50&tags=" + tag
                   + "&query=" + urllib.parse.quote(q)
                   + f"&numericFilters=created_at_i>{int(since.timestamp())}")
            raw = http(url)
            if not raw:
                continue
            for h in json.loads(raw).get("hits", []):
                text = clean(h.get("comment_text") or h.get("story_text") or "")
                title = h.get("title") or h.get("story_title") or ""
                oid = h.get("objectID")
                out.append({"id": cid("hn", oid), "source": "Hacker News", "room": "Hacker News",
                            "title": clean(title), "text": text,
                            "url": f"https://news.ycombinator.com/item?id={oid}",
                            "date": (h.get("created_at") or "")[:10],
                            "kind": "comment" if tag == "comment" else "thread"})
            time.sleep(0.3)
    return out


def from_stackexchange(queries, since):
    out = []
    for q in queries:
        url = ("https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=creation"
               "&site=security&filter=withbody&pagesize=30&q=" + urllib.parse.quote(q)
               + f"&fromdate={int(since.timestamp())}")
        raw = http(url, headers={"Accept-Encoding": "identity"})
        if not raw:
            continue
        try:
            items = json.loads(raw).get("items", [])
        except ValueError:
            continue
        for it in items:
            out.append({"id": cid("se", it.get("question_id")), "source": "Security Stack Exchange",
                        "room": "Security Stack Exchange", "title": clean(it.get("title")),
                        "text": clean(it.get("body")), "url": it.get("link", ""),
                        "date": datetime.fromtimestamp(it.get("creation_date", 0), timezone.utc).strftime("%Y-%m-%d"),
                        "kind": "question"})
        time.sleep(0.5)
    return out


def from_mastodon(tags, since):
    out = []
    for tag in tags:
        raw = http(f"https://infosec.exchange/tags/{urllib.parse.quote(tag)}.rss")
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            link = item.findtext("link") or ""
            try:
                dt = parsedate_to_datetime(item.findtext("pubDate"))
            except (TypeError, ValueError):
                dt = None
            if dt and dt < since:
                continue
            out.append({"id": cid("masto", link), "source": "Mastodon", "room": "infosec.exchange",
                        "title": "", "text": clean(item.findtext("description")), "url": link,
                        "date": dt.strftime("%Y-%m-%d") if dt else "", "kind": "post"})
        time.sleep(0.3)
    return out


def from_news(queries, since):
    out = []
    days = max(1, (NOW - since).days)
    for q in queries:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{days}d")
               + "&hl=en-US&gl=US&ceid=US:en")
        raw = http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            title = clean(item.findtext("title"))
            src = item.find("source")
            outlet = clean(src.text) if src is not None else "News"
            if title.endswith(" - " + outlet):
                title = title[: -len(outlet) - 3]
            try:
                dt = parsedate_to_datetime(item.findtext("pubDate"))
            except (TypeError, ValueError):
                dt = None
            out.append({"id": cid("news", title.lower()), "source": "Press", "room": outlet,
                        "title": title, "text": "", "url": item.findtext("link") or "",
                        "date": dt.strftime("%Y-%m-%d") if dt else "", "kind": "article"})
        time.sleep(0.5)
    return out


def reddit_token():
    cid_, sec = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid_ and sec):
        return None
    auth = base64.b64encode(f"{cid_}:{sec}".encode()).decode()
    raw = http("https://www.reddit.com/api/v1/access_token",
               headers={"Authorization": f"Basic {auth}"},
               data=b"grant_type=client_credentials")
    try:
        return json.loads(raw)["access_token"] if raw else None
    except (ValueError, KeyError):
        return None


def from_reddit(subs, queries, since):
    token = reddit_token()
    if not token:
        print("Reddit: no keys set, skipping")
        return []
    out = []
    hdr = {"Authorization": f"bearer {token}"}
    for sub in subs:
        for q in queries:
            url = (f"https://oauth.reddit.com/r/{sub}/search?restrict_sr=1&sort=new&t=month&limit=50&q="
                   + urllib.parse.quote(q))
            raw = http(url, headers=hdr)
            if not raw:
                continue
            for ch in json.loads(raw).get("data", {}).get("children", []):
                d = ch.get("data", {})
                dt = datetime.fromtimestamp(d.get("created_utc", 0), timezone.utc)
                if dt < since:
                    continue
                out.append({"id": cid("reddit", d.get("id")), "source": "Reddit", "room": f"r/{sub}",
                            "title": clean(d.get("title")), "text": clean(d.get("selftext")),
                            "url": "https://www.reddit.com" + d.get("permalink", ""),
                            "date": dt.strftime("%Y-%m-%d"), "kind": "thread"})
            time.sleep(1.1)
    return out


# ---------------- Tagging ----------------
def tag(conv, cfg):
    full = f" {conv['title']} {conv['text']} ".lower()
    personas = [k for k, v in cfg["buyers"].items() if any(hit(full, p) for p in v["phrases"])]
    pains = [k for k, v in cfg["pains"].items() if any(hit(full, p) for p in v["phrases"])]
    intent = [p.strip() for p in cfg["intent"]["phrases"] if hit(full, p)]
    conv["personas"], conv["pains"] = personas, pains
    conv["intent"] = bool(intent)
    return conv


def relevant(conv, cfg):
    full = f" {conv['title']} {conv['text']} ".lower()
    if any(x in full for x in cfg["relevance"]["exclude"]):
        return False
    if not any(hit(full, p) for p in cfg["relevance"]["phrases"]):
        return False
    return bool(conv["pains"]) or bool(conv["personas"])


INVITE = [
    (re.compile(r"discord\.(?:gg|com/invite)/([a-z0-9-]+)", re.I), "Discord", "https://discord.gg/{}"),
    (re.compile(r"join\.slack\.com/t/([a-z0-9-]+)", re.I), "Slack", "https://join.slack.com/t/{}"),
    (re.compile(r"([a-z0-9-]+)\.slack\.com", re.I), "Slack", "https://{}.slack.com"),
    (re.compile(r"(?<![a-z0-9])/?r/([a-z0-9_]{3,21})\b", re.I), "Reddit", "https://www.reddit.com/r/{}"),
]
NAMED = re.compile(r"([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,4})\s+((?i:slack|discord))(?:\s+(?i:group|community|server|workspace|channel))?")
NOT_NAMES = {"Our", "The", "A", "An", "My", "This", "That", "Company", "Work", "Internal", "Private", "Join",
             "On", "In", "Your", "Also", "And", "But", "Check", "Try", "Via", "See", "Use", "We", "I",
             "So", "Then", "Plus", "Or", "Their", "His", "Her", "Its", "Any", "Some", "Every", "Each",
             "Official", "Free", "Public", "New", "Great", "Good", "Best", "Popular", "Local", "Same"}


def room_key(name):
    base = re.sub(r"\((?:slack|discord|reddit)\)|\b(?:slack|discord)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", base)


def find_rooms(conv, directory):
    """Return the communities a conversation mentions, known or newly discovered."""
    raw = f" {conv['title']} {conv['text']} "
    low = raw.lower()
    found = {}

    def add(name, known, platform="", url=""):
        k = room_key(name)
        if not k:
            return
        cur = found.get(k)
        if cur is None or (known and not cur["known"]) or (not cur["known"] and not known and len(name.split()) > len(cur["name"].split())):
            found[k] = {"name": name, "known": known, "platform": platform or (cur or {}).get("platform", ""),
                        "url": url or (cur or {}).get("url", "")}
        elif url and not cur.get("url"):
            cur["url"] = url

    for r in directory:
        if any(hit(low, m) for m in r.get("match", [])):
            add(r["name"], True, r.get("platform", ""), r.get("url", ""))
    for rx, platform, tmpl in INVITE:
        for m in rx.finditer(raw):
            slug = m.group(1)
            if platform == "Slack" and slug.lower() in ("app", "www", "api", "join", "status", "hooks"):
                continue
            name = f"r/{slug}" if platform == "Reddit" else f"{slug} {platform}"
            known = next((r for r in directory if room_key(r["name"]) == room_key(name)), None)
            if known:
                add(known["name"], True, known.get("platform", ""), known.get("url", ""))
            else:
                add(name, False, platform, tmpl.format(slug))
    for m in NAMED.finditer(raw):
        words = m.group(1).split()
        while words and words[0] in NOT_NAMES:
            words = words[1:]
        if not words:
            continue
        plat = m.group(2).capitalize()
        name = " ".join(words) + " " + plat
        known = next((r for r in directory if room_key(r["name"]) == room_key(name)), None)
        if known:
            add(known["name"], True, known.get("platform", ""), known.get("url", ""))
        else:
            add(name, False, plat)
    return list(found.values())


# ---------------- Analysis ----------------
def rank_rooms(convs, directory, window):
    cutoff = NOW - timedelta(days=window)
    evidence = defaultdict(list)
    buyer_mentions = Counter()
    meta = {}
    for c in convs:
        d = day(c["date"])
        if d and d < cutoff:
            continue
        for r in c.get("rooms", []):
            evidence[r["name"]].append(c["id"])
            if c["personas"]:
                buyer_mentions[r["name"]] += 1
            if not r.get("known"):
                meta.setdefault(r["name"], r)
        # Where the conversation itself happened counts too.
        evidence.setdefault(c["room"], [])
    by_id = {c["id"]: c for c in convs}
    ranked = []
    names = {r["name"] for r in directory} | set(meta)
    for name in names:
        r = next((x for x in directory if x["name"] == name), None)
        ids = evidence.get(name, [])
        hosted = [c for c in convs if c["room"] == name and (not day(c["date"]) or day(c["date"]) >= cutoff)]
        hosted_buyers = sum(1 for c in hosted if c["personas"])
        fit = r.get("fit", 1) if r else 1
        mention_pts = round(math.log2(1 + len(ids)) * 2, 1)
        buyer_pts = round(math.log2(1 + buyer_mentions[name] + hosted_buyers) * 2, 1)
        fit_pts = fit * 2
        access_pts = {"open": 1, "event": 1, "apply": 0, "invite": 0, "paid": 0}.get(r.get("access", "open") if r else "unknown", 0)
        score = round(fit_pts + mention_pts + buyer_pts + access_pts, 1)
        why = [f"+{fit_pts} buyer fit ({fit} of 3)"]
        if ids:
            why.append(f"+{mention_pts} mentioned in {len(ids)} conversation{'s' if len(ids) != 1 else ''}")
        if buyer_mentions[name] or hosted_buyers:
            why.append(f"+{buyer_pts} buyers talking there or about it")
        if access_pts:
            why.append(f"+{access_pts} easy to join")
        if not r and len(ids) < 2:
            continue  # discovered rooms need at least two mentions to show up
        ranked.append({
            "name": name, "known": bool(r), "platform": (r or meta.get(name, {})).get("platform", ""),
            "audience": r.get("audience", "Discovered in practitioner conversations") if r else "Discovered in practitioner conversations",
            "access": r.get("access", "unknown") if r else "unknown",
            "who": r.get("who", "Check who runs it and whether vendors are welcome before joining.") if r else "Check who runs it and whether vendors are welcome before joining.",
            "url": (r or meta.get(name, {})).get("url", ""),
            "score": score, "why": why, "mentions": len(ids), "hosted": len(hosted),
            "evidence": [{"title": by_id[i]["title"] or by_id[i]["text"][:90], "url": by_id[i]["url"], "date": by_id[i]["date"]}
                         for i in ids[:5] if i in by_id],
        })
    ranked.sort(key=lambda x: -x["score"])
    return ranked


def pain_trends(convs, cfg):
    recent, prior = Counter(), Counter()
    for c in convs:
        d = day(c["date"])
        if not d:
            continue
        age = (NOW - d).days
        for p in c["pains"]:
            if age <= 14:
                recent[p] += 1
            elif age <= 28:
                prior[p] += 1
    weekly = defaultdict(lambda: [0] * 12)
    for c in convs:
        d = day(c["date"])
        if not d:
            continue
        wk = (NOW - d).days // 7
        if 0 <= wk < 12:
            for p in c["pains"]:
                weekly[p][11 - wk] += 1
    out = []
    for k, v in cfg["pains"].items():
        r, p = recent[k], prior[k]
        change = (round((r - p) / p * 100) if p else (100 if r else 0))
        out.append({"key": k, "label": v["label"], "recent": r, "prior": p, "change": change,
                    "weekly": weekly[k], "total": sum(1 for c in convs if k in c["pains"])})
    out.sort(key=lambda x: (-x["recent"], -x["total"]))
    return out


def buyer_phrases(convs, window=45, top=30):
    cutoff = NOW - timedelta(days=window)
    grams = Counter()
    seen_in = defaultdict(set)
    for c in convs:
        d = day(c["date"])
        if (d and d < cutoff) or not (c["personas"] or c["pains"]) or c["source"] == "Press":
            continue
        words = [w for w in re.findall(r"[a-z][a-z0-9'\-]+", f"{c['title']} {c['text']}".lower())]
        for n in (2, 3):
            for i in range(len(words) - n + 1):
                g = words[i:i + n]
                if g[0] in STOP or g[-1] in STOP or any(len(w) < 3 for w in g) or {"slack", "discord"} & set(g):
                    continue
                phrase = " ".join(g)
                if c["id"] not in seen_in[phrase]:
                    seen_in[phrase].add(c["id"])
                    grams[phrase] += 1
    out = []
    for phrase, n in grams.most_common(top * 3):
        if n < 2:
            break
        if any(phrase in o["phrase"] for o in out):
            continue
        out.append({"phrase": phrase, "count": n})
        if len(out) >= top:
            break
    return out


def pain_quotes(convs, cfg, per_pain=3):
    quotes = defaultdict(list)
    for c in sorted(convs, key=lambda x: x["date"] or "", reverse=True):
        if c["source"] == "Press":
            continue
        for sent in re.split(r"(?<=[.!?])\s+", c["text"]):
            words = sent.split()
            if not 6 <= len(words) <= 30:
                continue
            low = f" {sent.lower()} "
            for k, v in cfg["pains"].items():
                if len(quotes[k]) < per_pain and any(hit(low, p) for p in v["phrases"]):
                    quotes[k].append({"text": sent.strip(), "url": c["url"], "room": c["room"], "date": c["date"]})
                    break
    return quotes


# ---------------- AI (GitHub Models, free tier) ----------------
def ai(prompt, token, max_tokens=700):
    for model in MODELS:
        body = json.dumps({"model": model, "temperature": 0.4, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(MODEL_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            print(f"  AI call failed on {model}: HTTP {e.code} {e.read()[:300].decode('utf-8', 'replace')}")
            if e.code == 429:
                return None
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError) as e:
            print(f"  AI call failed on {model}: {e}")
    return None


STYLE = ("Plain text. No markdown, no asterisks, no bullets, no em dashes. Write like a sharp B2B marketer "
         "talking to a CEO. Use the buyers' own words where you can. Never invent facts, numbers or names.")


def brief_prompt(cfg, convs, trends, phrases):
    recent = [c for c in convs if day(c["date"]) and (NOW - day(c["date"])).days <= 14][:45]
    lines = "\n".join(f"- [{c['room']}] {c['title']} {c['text'][:200]}" for c in recent)
    rising = ", ".join(f"{t['label']} ({t['recent']} vs {t['prior']})" for t in trends[:5])
    words = ", ".join(p["phrase"] for p in phrases[:15])
    return (f"You advise the CEO and CMO of {cfg['company']}, a {cfg['category'].lower()} company. "
            f"Here is what their buyers (security leaders, vulnerability management owners, SecOps and IT) "
            f"said in public over the last two weeks:\n{lines}\n\nPain mentions, last 14 days vs the 14 before: {rising}\n"
            f"Phrases buyers repeat: {words}\n\n"
            "Write a buyer brief with these labeled sections, each two to three sentences:\n"
            "What buyers are talking about\nWhat changed\nWhat they are asking for\n"
            "Three moves for this week (one sentence each, numbered 1 to 3, covering content, sales and community)\n"
            "If the sample is thin, say so plainly. " + STYLE)


def reply_prompt(cfg, c):
    return (f"A practitioner posted this in {c['room']}:\nTitle: {c['title']}\nText: {c['text'][:900]}\n\n"
            f"You are a sales engineer at {cfg['company']} ({cfg['category'].lower()}). Write two labeled lines.\n"
            "Read: one sentence on what they actually need.\n"
            "Reply: a helpful public reply under 80 words that answers the question first, discloses you work at "
            f"{cfg['company']} if you mention it, and never sounds like a pitch. " + STYLE)


# ---------------- Main ----------------
def main():
    dry = "--dry-run" in sys.argv
    cfg = load_toml("config.toml")
    directory = load_toml("communities.toml").get("rooms", [])
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        data = {}
    convs = data.get("conversations", [])
    known = {c["id"] for c in convs}
    since = NOW - timedelta(days=cfg.get("first_run_days", 30) if not convs else 3)

    s = cfg["sources"]
    print("Collecting")
    raw = []
    if not dry or os.environ.get("HALLWAY_COLLECT"):
        raw += from_hn(s.get("hn_queries", []), since)
        raw += from_stackexchange(s.get("stackexchange_queries", []), since)
        raw += from_mastodon(s.get("mastodon_tags", []), since)
        raw += from_news(s.get("news_queries", []), since)
        raw += from_reddit(s.get("subreddits", []), s.get("reddit_queries", []), since)
    by_source = Counter()
    new = []
    for c in raw:
        if c["id"] in known:
            continue
        tag(c, cfg)
        if not relevant(c, cfg):
            continue
        known.add(c["id"])
        c["text"] = c["text"][:1200]
        c["rooms"] = find_rooms(c, directory)
        c["found"] = NOW.strftime("%Y-%m-%d")
        new.append(c)
        by_source[c["source"]] += 1
    print("New on-topic conversations:", dict(by_source) or 0)

    # Retag history so config edits apply everywhere, then keep the window plus a buffer.
    keep_days = cfg.get("window_days", 90) + 30
    convs = [c for c in convs + new if not day(c["date"]) or (NOW - day(c["date"])).days <= keep_days]
    for c in convs:
        tag(c, cfg)
        c["rooms"] = find_rooms(c, directory)
    convs = [c for c in convs if relevant(c, cfg)]
    convs.sort(key=lambda c: c["date"] or "", reverse=True)

    trends = pain_trends(convs, cfg)
    phrases = buyer_phrases(convs)
    rooms = rank_rooms(convs, directory, cfg.get("window_days", 90))
    quotes = pain_quotes(convs, cfg)

    token = os.environ.get("GITHUB_TOKEN")
    brief = data.get("brief", {})
    calls = 0
    if token and not dry and convs:
        last = day(brief.get("date", ""))
        if not last or (NOW - last).days >= 6 or not brief.get("text"):
            text = ai(brief_prompt(cfg, convs, trends, phrases), token)
            calls += 1
            if text:
                brief = {"date": NOW.strftime("%Y-%m-%d"), "text": text}
        for c in [c for c in convs if c["intent"] and c["source"] != "Press" and not c.get("reply")][:cfg.get("max_ai_calls", 12) - calls]:
            text = ai(reply_prompt(cfg, c), token, 300)
            calls += 1
            if text:
                c["reply"] = text
            time.sleep(3)
        print(f"AI calls: {calls}")

    out = {
        "generated": NOW.strftime("%Y-%m-%dT%H:%MZ"),
        "company": cfg["company"], "category": cfg["category"], "window_days": cfg.get("window_days", 90),
        "last_run": {"new": len(new), "by_source": dict(by_source)},
        "buyers": {k: v["label"] for k, v in cfg["buyers"].items()},
        "pains": {k: v["label"] for k, v in cfg["pains"].items()},
        "brief": brief, "trends": trends, "phrases": phrases, "quotes": quotes, "rooms": rooms,
        "conversations": convs,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"Saved {len(convs)} conversations, {len(rooms)} rooms ranked.")


if __name__ == "__main__":
    main()
