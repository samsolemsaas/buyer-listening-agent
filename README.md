# Hallway Track

Buyer listening for B2B teams. Hallway Track tracks what a company's buyers say in public, the words they use, what's getting louder, and which communities the team should be in.

Competitive intel watches vendors. Hallway Track watches buyers.

Built by Samantha Solemsaas.

## What it answers

- **What are buyers struggling with right now, and what's rising?** Pains are tracked week over week in the buyers' own language.
- **Who is shopping?** Public threads where practitioners ask for tools or advice get flagged, with a suggested reply that helps first and never pitches.
- **Which rooms should our people be in?** Communities are ranked by buyer fit and by how often practitioners mention them, each with a note on who should join (founder, SE, AE, field marketing) and links to the evidence.
- **What words should our content use?** The phrases buyers repeat, plus short quotes tied to each pain.
- **What should we do this week?** An AI-written buyer brief every week.

## How it works

1. **Listen.** Every morning it collects public conversations from Hacker News, Information Security Stack Exchange, infosec.exchange (Mastodon), Google News and, with free API keys, Reddit.
2. **Tag.** Rules in `config.toml` mark who is talking (security leader, vuln management owner, SecOps, IT), what hurts, and whether they're shopping.
3. **Find the rooms.** It spots communities people point each other to, like Slack groups, Discord servers, subreddits and events, and checks them against a directory in `communities.toml`. Rooms it hasn't seen before show up as "Discovered."
4. **Brief.** GitHub Models (free) writes the weekly brief and suggested replies.
5. **Publish.** Everything lands in `hallway.json`, and `index.html` turns it into the dashboard on GitHub Pages.

## Privacy

It reads public posts only. It never joins or reads private groups, it doesn't store usernames, and every excerpt links to the original. Rooms are recommended from public evidence so a team can request access the normal way.

## Point it at a different company

Edit `config.toml`: the company, buyer personas, pains, buying intent phrases and searches. Add rooms to `communities.toml`.

## Cost

$0. GitHub Actions, GitHub Pages and the GitHub Models free tier cover it. Reddit is optional and uses Reddit's free API.
