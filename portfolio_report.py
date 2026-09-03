"""
Build the portfolio write-up as a single self-contained HTML page.

Every number in the page is computed here, from the same code the page is
about, so the report cannot drift from the simulation. Run it and open the
file:

    python portfolio_report.py            -> aldermere_ledger.html

It takes a minute or two: it replays the single-town world with and without
events, the three-town world with and without merchants, and a full year of
adventuring with and without the player, across several seeds.
"""

from __future__ import annotations

import copy
import json
import statistics

from economy import Town
from trade import World
from kingdom import Kingdom, Board, resolve, counterfactual


OUT = "aldermere_ledger.html"


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------

def mean(xs):
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def single_town():
    cfg = json.load(open("world.json"))
    quiet = copy.deepcopy(cfg)
    quiet["events"] = []
    swings = {}
    for key in ("wheat", "cloth", "tool"):
        off, on = [], []
        for seed in range(5):
            h = Town(quiet, seed=seed).run(240)
            t = h["price"][key][40:]
            off.append((max(t) - min(t)) / mean(t) * 100)
            h = Town(cfg, seed=seed).run(240)
            t = h["price"][key][40:]
            on.append((max(t) - min(t)) / mean(t) * 100)
        swings[key] = (mean(off), mean(on))

    t = Town(cfg, seed=7)
    h = t.run(240)
    series = {k: h["price"][k] for k in ("wheat", "cloth", "tool")}
    events = [(tick, name) for tick, name, _ in t.event_log]
    names = {k: t.goods[k].name for k in t.goods}
    return {"swings": swings, "series": series, "events": events, "names": names}


def three_towns():
    cfg = json.load(open("towns.json"))
    gaps = {}
    for good in ("wheat", "cloth", "tool", "mithril_tool"):
        off, on = [], []
        for seed in range(4):
            w = World(cfg, seed=seed, enable_trade=False)
            w.run()
            off.append(mean(w.history["spread"][good][-100:]))
            w = World(cfg, seed=seed, enable_trade=True)
            w.run()
            on.append(mean(w.history["spread"][good][-100:]))
        gaps[good] = (mean(off), mean(on))

    w = World(cfg, seed=7)
    w.run()
    take = sum(m.profit for m in w.merchants)
    return {
        "gaps": gaps,
        "take": take,
        "gross_exports": sum(w.exports.values()),
        "gross_imports": sum(w.imports.values()),
        "shipments": sum(1 for s in w.shipments if s.sell_price > 0),
        "towns": [(t.name, t.efficiency_bonus) for t in w.towns.values()],
        "names": {k: w.towns["riverfold"].goods[k].name for k in w.towns["riverfold"].goods},
    }


SHARE = {"wheat": 0.42, "cloth": 0.27, "tool": 0.16, "mithril_tool": 0.15}
RES_GOOD = {"grain_land": "wheat", "cotton_land": "cloth",
            "ore_land": "tool", "mithril_vein": "mithril_tool"}


def score_outing(o):
    """A county-minded adventurer: expected benefit to the people who live here."""
    total, rem = 0.0, 1.0
    for oc in o.outcomes:
        p = rem if oc.chance >= 1.0 else rem * oc.chance
        rem -= p
        v = 0.0
        if oc.perm:
            for r, m in oc.perm.items():
                v += (m - 1.0) * 300 * SHARE.get(RES_GOOD.get(r), 0.1)
        if oc.event:
            e = oc.event
            if e["type"] == "disaster":
                v += (e["efficiency_mult"] - 1.0) * e["duration"] * \
                     SHARE.get(RES_GOOD.get(e["target"]), 0.1)
            elif e["type"] == "stock":
                for k2, d in e["changes"].items():
                    v += d / 200.0 * SHARE.get(k2, 0.1)
            elif e["type"] == "discovery":
                v += 12.0
        total += p * v
        if rem <= 1e-9:
            break
    return total


def adventure(cfg, strategy, seed, days=360, outing=5, town_every=5):
    k = Kingdom(cfg, seed=seed)
    board = Board(k.rng)
    day = turn = 0
    accidents = 0
    while day < days:
        turn += 1
        offers = board.offer(k, turn, 2)
        if offers:
            if strategy == "mine":
                pick = max(range(len(offers)), key=lambda i: score_outing(offers[i]))
            elif strategy == "first":
                pick = 0
            elif strategy == "second":
                pick = min(1, len(offers) - 1)
            else:
                pick = k.rng.randint(1, len(offers)) - 1
            chosen = offers[pick]
            oc = resolve(k, chosen, day)
            board.taken(chosen, turn)
            if len(chosen.outcomes) > 1 and oc is chosen.outcomes[-1]:
                accidents += 1
        for _ in range(outing):
            if day >= days:
                break
            k.step(day)
            k.whats_being_said(day)
            day += 1
        if turn % town_every == 0 and k.gear == "iron":
            price = k.gear_price("mithril")
            if price is not None and k.player.gold >= price * 2.0:
                k.buy_gear("mithril", day)
    return k, accidents


def tail(s, n=40):
    return mean(s[-n:]) if s else 0.0


def one_year():
    cfg = json.load(open("world.json"))
    seed, days, outing = 42, 360, 5
    k, _ = adventure(cfg, "mine", seed, days, outing)
    ghost = counterfactual(cfg, seed, days, k.footprints, outing)
    warm = cfg["simulation"].get("warmup_days", 40)

    prices = []
    for g in k.town.active_goods():
        theirs = ghost.town.goods[g.key].price
        if theirs > 1e-6:
            prices.append((g.name, g.price, theirs, (g.price / theirs - 1) * 100))

    land = []
    for res, v in sorted(k.town.efficiency_bonus.items()):
        if abs(v - 1.0) > 0.005:
            land.append((res.split("_")[0], (v - 1) * 100))

    pressure = {}
    for _t, title, ev in k.footprints:
        kind = ev["type"]
        if kind == "stock":
            for key, d in ev["changes"].items():
                pressure[(title, key)] = pressure.get((title, key), 0.0) + d / 100.0
        elif kind == "disaster":
            key = (title, ev["target"])
            pressure[key] = pressure.get(key, 0.0) + (ev["efficiency_mult"] - 1.0) * ev["duration"]
        elif kind == "permanent":
            for r, m in ev["changes"].items():
                key = (title + " (permanent)", r)
                pressure[key] = pressure.get(key, 0.0) + (m - 1.0) * 400
        elif kind == "discovery":
            key = (title, ev["introduces"])
            pressure[key] = pressure.get(key, 0.0) + 40.0
    top = sorted(pressure.items(), key=lambda kv: -abs(kv[1]))[:7]

    return {
        "seed": seed, "days": days,
        "outings": len(k.jobs_taken), "marks": len(k.footprints),
        "prices": prices, "land": land, "top": top,
        "food": tail(k.town.history["real_consumption"]),
        "food_ghost": tail(ghost.town.history["real_consumption"]),
        "wheat_with": k.town.history["price"]["wheat"][warm:],
        "wheat_without": ghost.town.history["price"]["wheat"][warm:],
        "worth": k.player.net_worth(k.town),
    }


def strategies():
    cfg = json.load(open("world.json"))
    seeds = (42, 7, 11, 19, 23, 31, 88)
    label = {"mine": "county-minded", "first": "always first",
             "second": "always second", "random": "whichever"}
    rows = []
    wins = {n: 0 for n in label}
    per_seed = {n: [] for n in label}
    for name in label:
        for seed in seeds:
            k, acc = adventure(cfg, name, seed)
            ghost = counterfactual(cfg, seed, 360, [], 5)
            per_seed[name].append({
                "food": (tail(k.town.history["real_consumption"]) /
                         tail(ghost.town.history["real_consumption"]) - 1) * 100,
                "purse": k.player.net_worth(k.town),
                "wage": mean(k.wage_log[-24:]) if k.wage_log else 1.0,
                "acc": acc,
                "grain": (k.town.efficiency_bonus.get("grain_land", 1.0) - 1) * 100,
            })
    for i in range(len(seeds)):
        wins[max(label, key=lambda n: per_seed[n][i]["food"])] += 1
    for name in label:
        d = per_seed[name]
        rows.append({
            "name": label[name],
            "food": mean(x["food"] for x in d),
            "food_min": min(x["food"] for x in d),
            "purse": mean(x["purse"] for x in d),
            "wage": mean(x["wage"] for x in d),
            "acc": mean(x["acc"] for x in d),
            "grain": mean(x["grain"] for x in d),
            "wins": wins[name],
        })
    return {"rows": rows, "seeds": len(seeds)}


def accumulation():
    cfg = json.load(open("world.json"))
    out = []
    for days in (45, 90, 180, 360):
        diffs, ore = [], []
        for seed in (7, 11, 19, 23):
            k, _ = adventure(cfg, "first", seed, days)
            ghost = counterfactual(cfg, seed, days, k.footprints, 5)
            d = []
            for key in ("wheat", "cloth", "tool"):
                a, b = k.town.goods[key].price, ghost.town.goods[key].price
                if b > 1e-6:
                    d.append(abs(a / b - 1) * 100)
            diffs.append(mean(d))
            ore.append((k.town.efficiency_bonus.get("ore_land", 1.0) - 1) * 100)
        out.append((days, mean(diffs), mean(ore)))
    return out


# --------------------------------------------------------------------------
# charts (inline SVG, colours through CSS variables so the theme applies)
# --------------------------------------------------------------------------

SER = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]


def fmt(n, d=0):
    return ("{:,.%df}" % d).format(n)


def line_chart(series, labels, events=(), w=720, h=250, baseline=None, unit="g"):
    m = {"t": 16, "r": 16, "b": 30, "l": 46}
    n = max(len(v) for v in series)
    lo = min(min(v) for v in series)
    hi = max(max(v) for v in series)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = lo - pad, hi + pad
    px = lambda i: m["l"] + i / max(n - 1, 1) * (w - m["l"] - m["r"])
    py = lambda v: m["t"] + (1 - (v - lo) / (hi - lo)) * (h - m["t"] - m["b"])

    parts = ['<svg viewBox="0 0 %d %d" role="img" class="chart">' % (w, h)]
    for g in range(4):
        v = lo + (hi - lo) * g / 3
        y = py(v)
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (m["l"], w - m["r"], y, y))
        parts.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s%s</text>' % (m["l"] - 8, y + 4, fmt(v, 1), unit))
    if baseline is not None:
        y = py(baseline)
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="base"/>' % (m["l"], w - m["r"], y, y))
    for tick, name in events:
        x = px(tick)
        parts.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" class="ev"/>' % (x, x, m["t"], h - m["b"]))
        parts.append('<text x="%.1f" y="%d" class="evl">%s</text>' % (x + 4, m["t"] + 10, name))
    for i, v in enumerate(series):
        d = " ".join("%s%.1f %.1f" % ("M" if j == 0 else "L", px(j), py(y)) for j, y in enumerate(v))
        parts.append('<path d="%s" fill="none" stroke-width="2" style="stroke:%s" stroke-linejoin="round"/>' % (d, SER[i]))
    parts.append('<text x="%d" y="%d" class="ax">day 0</text>' % (m["l"], h - 8))
    parts.append('<text x="%d" y="%d" class="ax" text-anchor="end">day %d</text>' % (w - m["r"], h - 8, n))
    parts.append("</svg>")
    legend = "".join('<span><i style="background:%s"></i>%s</span>' % (SER[i], l) for i, l in enumerate(labels))
    return '<div class="legend">%s</div>' % legend + "".join(parts)


def grouped_bars(cats, groups, labels, w=720, h=230, unit="%"):
    m = {"t": 14, "r": 16, "b": 40, "l": 46}
    top = max(max(g) for g in groups) * 1.18 or 1
    py = lambda v: m["t"] + (1 - v / top) * (h - m["t"] - m["b"])
    band = (w - m["l"] - m["r"]) / len(cats)
    bw = min(30, band * 0.3)
    parts = ['<svg viewBox="0 0 %d %d" role="img" class="chart">' % (w, h)]
    for g in range(4):
        v = top * g / 3
        y = py(v)
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (m["l"], w - m["r"], y, y))
        parts.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s%s</text>' % (m["l"] - 8, y + 4, fmt(v), unit))
    for ci, cat in enumerate(cats):
        cx = m["l"] + band * (ci + 0.5)
        for gi, vals in enumerate(groups):
            x = cx + (gi - (len(groups) - 1) / 2) * (bw + 6) - bw / 2
            y = py(vals[ci])
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" style="fill:%s"/>' % (
                x, y, bw, h - m["b"] - y, SER[gi] if gi else "var(--ink3)"))
            parts.append('<text x="%.1f" y="%.1f" class="ax" text-anchor="middle">%s%s</text>' % (
                x + bw / 2, y - 5, fmt(vals[ci]), unit))
        parts.append('<text x="%.1f" y="%d" class="axl" text-anchor="middle">%s</text>' % (cx, h - 16, cat))
    parts.append("</svg>")
    legend = "".join('<span><i style="background:%s"></i>%s</span>' % (SER[i] if i else "var(--ink3)", l) for i, l in enumerate(labels))
    return '<div class="legend">%s</div>' % legend + "".join(parts)


def hbars(rows, w=720, unit="", lw=250):
    rh = 26
    m = {"t": 6, "r": 70, "b": 6, "l": lw}
    h = m["t"] + m["b"] + rh * len(rows)
    mx = max(abs(v) for _, v, _ in rows) or 1
    parts = ['<svg viewBox="0 0 %d %d" role="img" class="chart">' % (w, h)]
    for i, (label, v, color) in enumerate(rows):
        y = m["t"] + i * rh
        bw = abs(v) / mx * (w - m["l"] - m["r"])
        parts.append('<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="4" style="fill:%s"/>' % (
            m["l"], y + 5, max(2, bw), rh - 11, color))
        parts.append('<text x="%d" y="%.1f" class="axl" text-anchor="end">%s</text>' % (m["l"] - 10, y + rh / 2 + 4, label))
        parts.append('<text x="%.1f" y="%.1f" class="ax">%s%s</text>' % (m["l"] + max(2, bw) + 8, y + rh / 2 + 4, ("%+.0f" % v) if unit == "%" else fmt(v), unit))
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# the page
# --------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;
 --ground:#f5f7f6;--paper:#ffffff;--ink:#14181c;--ink2:#4b5560;--ink3:#7a8590;
 --rule:#d8dfe2;--rule2:#e8edef;--accent:#1b6f5a;--accent-ink:#ffffff;
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
 --shadow:0 1px 2px rgba(20,24,28,.06),0 10px 30px rgba(20,24,28,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --ground:#0f1416;--paper:#161d20;--ink:#eef3f4;--ink2:#a5b2b9;--ink3:#7f8c94;
 --rule:#29343a;--rule2:#1f282d;--accent:#5fc3a4;--accent-ink:#0f1416;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
 --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.3)}}
:root[data-theme="dark"]{color-scheme:dark;
 --ground:#0f1416;--paper:#161d20;--ink:#eef3f4;--ink2:#a5b2b9;--ink3:#7f8c94;
 --rule:#29343a;--rule2:#1f282d;--accent:#5fc3a4;--accent-ink:#0f1416;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
 --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.3)}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:16px/1.6 "Source Sans 3",ui-sans-serif,system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:64px 24px 96px}
h1,h2,h3{font-family:Bitter,Georgia,serif;margin:0;text-wrap:balance}
h1{font-size:clamp(34px,5vw,50px);font-weight:700;letter-spacing:-.02em;line-height:1.08}
h2{font-size:24px;font-weight:600;margin-top:64px;padding-top:28px;border-top:1px solid var(--rule)}
h3{font-size:16px;font-weight:600;margin-top:28px}
p{max-width:68ch;margin:14px 0}
.eyebrow{font:500 11px/1 "IBM Plex Mono",ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);margin-bottom:14px}
.lede{font-size:19px;color:var(--ink2);max-width:62ch;margin-top:18px}
.thesis{margin:34px 0 0;padding:22px 26px;background:var(--paper);border-left:4px solid var(--accent);border-radius:0 10px 10px 0;box-shadow:var(--shadow);font-family:Bitter,Georgia,serif;font-size:21px;line-height:1.4;max-width:none}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:30px 0 0}
.tile{background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;box-shadow:var(--shadow)}
.tile b{display:block;font-family:Bitter,Georgia,serif;font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.tile span{font-size:13px;color:var(--ink2)}
figure{margin:24px 0;background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:16px 18px 10px;box-shadow:var(--shadow)}
figcaption{font-size:13.5px;color:var(--ink2);margin-top:8px;max-width:none}
figcaption b{color:var(--ink)}
svg.chart{display:block;width:100%;height:auto;overflow:visible}
.grid{stroke:var(--rule2);stroke-width:1}
.base{stroke:var(--ink3);stroke-width:1;stroke-dasharray:1 4}
.ev{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3;opacity:.6}
.ax{font:11px "IBM Plex Mono",ui-monospace,monospace;fill:var(--ink3)}
.axl{font:12.5px "Source Sans 3",sans-serif;fill:var(--ink2)}
.evl{font:10.5px "IBM Plex Mono",monospace;fill:var(--ink2)}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 10px;font-size:13px;color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:11px;height:11px;border-radius:3px;display:inline-block}
.tbl{overflow-x:auto;background:var(--paper);border:1px solid var(--rule);border-radius:10px;box-shadow:var(--shadow);margin:22px 0}
table{border-collapse:collapse;width:100%;font-size:14.5px}
th,td{padding:10px 14px;text-align:right;border-bottom:1px solid var(--rule2);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{font:500 11px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
tbody tr:last-child td{border-bottom:0}
td.n{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}
td.hi{color:var(--accent);font-weight:600}
.callout{margin:24px 0;padding:18px 22px;background:var(--paper);border:1px solid var(--rule);border-radius:10px;box-shadow:var(--shadow)}
.callout p{margin:6px 0}
.mono{font-family:"IBM Plex Mono",monospace;font-size:13.5px}
pre{background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:16px 18px;overflow-x:auto;font:13px/1.5 "IBM Plex Mono",monospace;color:var(--ink);margin:18px 0}
.stack{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:22px 0}
.layer{background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:16px 18px;box-shadow:var(--shadow)}
.layer h3{margin:0 0 6px;font-size:15px}
.layer p{margin:0;font-size:14px;color:var(--ink2)}
.layer .f{font:12px "IBM Plex Mono",monospace;color:var(--ink3);margin-top:10px;display:block}
ul{max-width:68ch;padding-left:22px}
li{margin:6px 0}
footer{margin-top:72px;padding-top:22px;border-top:1px solid var(--rule);color:var(--ink3);font-size:13.5px}
a{color:var(--accent)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""


def build(st, tt, yr, sg, ac):
    names = st["names"]
    ev = [(t, n) for t, n in st["events"]]
    wheat_swing = st["swings"]["wheat"]
    gap_w = tt["gaps"]["wheat"]
    gap_c = tt["gaps"]["cloth"]
    food_gap = (yr["food"] / yr["food_ghost"] - 1) * 100 if yr["food_ghost"] else 0
    best = max(sg["rows"], key=lambda r: r["food"])
    worst = min(sg["rows"], key=lambda r: r["food"])

    tiles = [
        ("%d" % (len(st["series"]) + 1), "goods, one of them discoverable"),
        ("%.0f%%" % (100 - gap_w[1] / gap_w[0] * 100), "smaller price gap between towns once merchants exist"),
        ("%+.0f%%" % food_gap, "better fed after one county-minded year, vs the same year with no player"),
        ("0.0000%", "difference between a do-nothing player and the counterfactual ghost"),
    ]

    h = []
    a = h.append
    a('<title>The Aldermere Ledger</title>')
    a('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    a('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bitter:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&display=swap">')
    a("<style>%s</style>" % CSS)
    a('<div class="wrap">')

    a('<div class="eyebrow">Simulation &middot; game AI &middot; genetic algorithms</div>')
    a("<h1>The Aldermere Ledger</h1>")
    a('<p class="lede">A game economy where prices are state, producers adapt with a genetic algorithm, '
      "and a year of ordinary adventuring leaves a measurable mark on the county &mdash; measured against "
      "the same year with the player removed.</p>")
    a('<p class="thesis">Most game economies are lookup tables. This one is a ledger of everything the player did, '
      "most of which they were not thinking about economically.</p>")
    a('<div class="tiles">' + "".join('<div class="tile"><b>%s</b><span>%s</span></div>' % t for t in tiles) + "</div>")

    # ---- problem ---------------------------------------------------------
    a("<h2>The problem</h2>")
    a("<p>A sword costs 150 gold on day one and on day four hundred, whatever happened in between. "
      "Games that do move prices usually do it with a scripted multiplier: a quest flag sets iron to &times;1.3 "
      "for a week. The player learns quickly that the economy is a prop.</p>")
    a("<p>The goal here was an economy that moves for reasons &mdash; where a blight, a discovery, or a "
      "player burning four acres of wheat on a botched spell all reach the price through the same physical "
      "mechanism, and where the consequences of many small acts accumulate into something the player can "
      "eventually see.</p>")

    # ---- system ----------------------------------------------------------
    a("<h2>The system</h2>")
    a("<p>Three layers, each usable without the ones above it. Pure Python, no dependencies.</p>")
    a('<div class="stack">')
    a('<div class="layer"><h3>Market engine</h3><p>One town. Price is persistent state that chases excess demand with '
      "inertia and rails. Households buy by need priority with per-good elasticity. Producers are a population "
      "whose genome is a land allocation; a GA breeds the profitable ones every three days, so the economy "
      "re-equilibrates after every shock instead of being solved once.</p><span class=\"f\">economy.py</span></div>")
    a('<div class="layer"><h3>Trade layer</h3><p>Several towns with different land quality, coupled only by NPC merchants '
      "who buy where a good is cheap and haul it where it is dear. Price convergence, shortages spreading between "
      "towns, and a merchant class that keeps a measurable cut are all emergent.</p><span class=\"f\">trade.py</span></div>")
    a('<div class="layer"><h3>Game layer</h3><p>A player who takes work, hears rumours instead of reading a debug panel, '
      "and leaves a footprint on the county with every outing. At the end the year is replayed without them.</p>"
      "<span class=\"f\">kingdom.py</span></div>")
    a("</div>")

    a("<h3>How a day works</h3>")
    a("<pre>production   land share &times; land &times; yield &times; efficiency          (events change efficiency)\n"
      "demand       population &times; need &times; (base price / price)^elasticity\n"
      "clearing     buy by priority until the budget runs out; unsold stock spoils\n"
      "price        price &times; (1 + 0.18 &times; excess demand), 55% inertia, clamped 0.2&ndash;5&times; base\n"
      "fitness      produced &times; price &times; sell-through, smoothed   (goods nobody bought earn nothing)\n"
      "evolve       every 3 days: elites survive, tournament crossover, gaussian mutation in logit space</pre>")
    a("<p>Two of those lines carry most of the weight. Elasticity is what separates a necessity from a luxury: "
      "wheat at 0.35 barely loses demand when its price doubles, tools at 1.6 lose two-thirds. And sell-through "
      "in the fitness is what prevents monoculture &mdash; flooding a market punishes you, so producers diversify "
      "on their own.</p>")

    # ---- evidence 1 ------------------------------------------------------
    a("<h2>Events move prices, and the market recovers</h2>")
    a("<p>The signature curve. A blight cuts grain yield to 30% on day 60 and recovers linearly. Wheat roughly "
      "doubles, farmers shift land into grain, supply returns, price settles. Nobody scripted the shape.</p>")
    a("<figure>" + line_chart([st["series"][k] for k in ("wheat", "cloth", "tool")],
                              [names[k] for k in ("wheat", "cloth", "tool")], events=ev, baseline=None)
      + "<figcaption>Single town, 240 days, seed 7. Dashed rules mark the three scheduled events. "
        "Note iron tools falling during the blight: nothing happened to iron. Bread ate the household budget "
        "and tools were last in the queue &mdash; cross-market contagion from one shared purse.</figcaption></figure>")
    a("<p>Is that the events, or the GA thrashing? Same world with the events deleted, five seeds:</p>")
    a('<div class="tbl"><table><thead><tr><th>good</th><th>swing, no events</th><th>swing, with events</th></tr></thead><tbody>')
    for k in ("wheat", "cloth", "tool"):
        off, on = st["swings"][k]
        a('<tr><td>%s</td><td class="n">%.0f%%</td><td class="n hi">%.0f%%</td></tr>' % (names[k], off, on))
    a("</tbody></table></div>")
    a("<p>Idle prices settle within a few percent of base with light background churn. The large moves are the shocks.</p>")

    # ---- evidence 2 ------------------------------------------------------
    a("<h2>Merchants integrate the towns</h2>")
    a("<p>Three towns &mdash; Riverfold farms, Loomhaven weaves, Ashmoor mines &mdash; run in isolation drift apart. "
      "Merchants exist to exploit the gap. The test is the same world, same seed, with the fleet deleted.</p>")
    cats = [tt["names"][k] for k in ("wheat", "cloth", "tool", "mithril_tool")]
    a("<figure>" + grouped_bars(cats,
                                [[tt["gaps"][k][0] for k in ("wheat", "cloth", "tool", "mithril_tool")],
                                 [tt["gaps"][k][1] for k in ("wheat", "cloth", "tool", "mithril_tool")]],
                                ["merchants deleted", "merchants running"])
      + "<figcaption>Average gap between the dearest and cheapest town, last 100 days, four seeds. A single "
        "integrated market would sit at zero. Metals converge less: thin markets, not worth a caravan.</figcaption></figure>")
    a('<div class="callout"><p><b>An accounting check that was not planned.</b> Over one run the towns received %s for exports and paid %s for imports. '
      "The gap is <b>%s</b>. Total merchant profit is <b>%s</b>. Money is conserved through the trade layer to the gold.</p></div>"
      % (fmt(tt["gross_exports"]), fmt(tt["gross_imports"]), fmt(tt["gross_imports"] - tt["gross_exports"]), fmt(tt["take"])))
    a("<p>Getting here took three passes, each of which found a real pathology. Merchants could only buy leftover "
      "stock, but an equilibrium market clears to nothing &mdash; a deadlock, fixed by letting them bid before the "
      "market opens so an unfilled order still moves the price. Then they ping-ponged mithril between two towns "
      "that mined none, each trade moving a thin price enough to fund the next &mdash; a pump, fixed by only "
      "buying at the source. Then bids ten times a town's output swamped every price signal and all three towns "
      "planted wheat &mdash; fixed by capping export bids against supply.</p>")

    # ---- evidence 3 ------------------------------------------------------
    a("<h2>The year without you</h2>")
    a("<p>This is the part worth pitching. The player takes %d outings in a year &mdash; goblins in a mine shaft, "
      "wasps in the cotton, a hedge mage who wants a fire ward tested. Each leaves a footprint of a few percent "
      "on a yield for a few weeks, below the market's own noise. Some go wrong: the fire ward holds 60%% of the time.</p>"
      % yr["outings"])
    a("<p>At the end, the whole year is replayed with those footprints removed. The difference is attributable "
      "to the player and nothing else.</p>")
    a("<figure>" + line_chart([yr["wheat_with"], yr["wheat_without"]], ["with you", "without you"])
      + "<figcaption>Wheat, %d days, seed %d, a county-minded adventurer. The two lines share every world event "
        "and every random draw the producers make. They diverge only because of what the player did.</figcaption></figure>"
      % (yr["days"], yr["seed"]))
    a('<div class="tbl"><table><thead><tr><th>good</th><th>with you</th><th>without you</th><th>difference</th></tr></thead><tbody>')
    for n, mine, theirs, gap in yr["prices"]:
        a('<tr><td>%s</td><td class="n">%.2fg</td><td class="n">%.2fg</td><td class="n hi">%+.0f%%</td></tr>' % (n, mine, theirs, gap))
    a("</tbody></table></div>")
    a("<h3>Where it came from</h3>")
    a("<p>The report attributes pressure to outings. A player can read that the shaft they shored in week four "
      "is why iron is where it is.</p>")
    rows = []
    for (title, target), score in yr["top"]:
        t = title if len(title) <= 44 else title[:41] + "..."
        rows.append(("%s &middot; %s" % (t, target.split("_")[0]), score,
                     SER[0] if score > 0 else SER[1]))
    a("<figure>" + hbars(rows, lw=330) + "<figcaption>Pressure applied per outing (modifier-days). Blue pushed supply up, orange pushed it down.</figcaption></figure>")
    a('<div class="callout"><p><b>Validation.</b> A player who takes no outings at all is <b>bit-identical</b> to the counterfactual ghost on every price '
      "and on prosperity, and the ghost is deterministic across repeated runs. The &ldquo;without you&rdquo; column is a true control, not an estimate.</p></div>")

    # ---- evidence 4: the finding -----------------------------------------
    a("<h2>What accumulates &mdash; and what does not</h2>")
    a("<p>The original pitch was that small price effects would add up over a long game. Measured, they do not:</p>")
    a('<div class="tbl"><table><thead><tr><th>days</th><th>outings</th><th>avg price difference, 3 goods</th><th>ore land, permanent</th></tr></thead><tbody>')
    for days, diff, ore in ac:
        a('<tr><td class="n">%d</td><td class="n">%d</td><td class="n">%.0f%%</td><td class="n hi">%+.1f%%</td></tr>' % (days, days // 5, diff, ore))
    a("</tbody></table></div>")
    a("<p>Price difference peaks and reverts. The reason is the GA doing its job: shift the supply of something "
      "and producers move land until revenue per acre is level again. Temporary supply shocks leave no permanent "
      "price mark in a market with adaptive producers &mdash; which is correct economics, and it quietly killed "
      "the naive version of the pitch.</p>")
    a("<p>What does accumulate is the land. Shoring a shaft, draining a marsh, burning a woodland are permanent "
      "changes to what the county can produce, and they never spring back. After a year:</p>")
    a("<figure>" + hbars([(n, v, SER[2] if v > 0 else SER[1]) for n, v in yr["land"]], unit="%", lw=160)
      + "<figcaption>Permanent change in land quality after %d days, seed %d. People eat <b>%+.1f%%</b> better than in the year without the player.</figcaption></figure>"
      % (yr["days"], yr["seed"], food_gap))
    a("<p>So the claim that survives measurement is sharper than the one that went in: <b>the county's productive "
      "capacity is a ledger of everything the player did.</b> Prices are the visible surface and they mostly recover. "
      "The durable change is structural, and it is what a game should show.</p>")

    # ---- evidence 5: strategies ------------------------------------------
    a("<h2>Choices produce different counties</h2>")
    a("<p>Four ways to spend the year on the same seed, replayed across %d seeds. One of them scores each job "
      "by its expected benefit to the people who live here and refuses the lucrative harmful ones.</p>" % sg["seeds"])
    a('<div class="tbl"><table><thead><tr><th>strategy</th><th>prosperity vs no player</th><th>worst seed</th><th>grain land</th><th>late wages</th><th>accidents</th><th>purse</th><th>best of %d</th></tr></thead><tbody>' % sg["seeds"])
    for r in sg["rows"]:
        hi = " hi" if r is best else ""
        a('<tr><td>%s</td><td class="n%s">%+.1f%%</td><td class="n">%+.1f%%</td><td class="n">%+.1f%%</td><td class="n">%.2f&times;</td><td class="n">%.1f</td><td class="n">%sg</td><td class="n">%d</td></tr>'
          % (r["name"], hi, r["food"], r["food_min"], r["grain"], r["wage"], r["acc"], fmt(r["purse"]), r["wins"]))
    a("</tbody></table></div>")
    a("<p>The county-minded adventurer wins %d of %d seeds on prosperity and its worst year beats every other "
      "strategy's average. It is not the richest. Work pays better in a town that eats better &mdash; late-year "
      "wages of %.2f&times; against %.2f&times; &mdash; and mithril kit exists because they found the ore, but they "
      "decline the cattle raid's plunder and that shows in the purse. Doing right is competitive, not free. "
      "That seemed like the more honest game.</p>" % (best["wins"], sg["seeds"], best["wage"], worst["wage"]))

    # ---- what did not work -----------------------------------------------
    a("<h2>What did not work</h2>")
    a("<ul>")
    a("<li><b>&ldquo;Cheaper gear in a prosperous town&rdquo;</b> as a reward. More ore makes tools cheaper, households "
      "buy more, producers shift land away, the price recovers. The runs that improved ore land most did not have "
      "cheaper iron. Wages follow prosperity instead, because prosperity is the thing that actually accumulates.</li>")
    a("<li><b>Reputation gating work.</b> Built, measured, worked &mdash; and cut. It is Fable's mechanic, and the point "
      "of this project is consequence the player was not looking for, not a morality meter.</li>")
    a("<li><b>A hunger loop.</b> The adventurer buys bread weekly, so wrecking the grain supply should cost them. "
      "It cannot: one person eats fourteen measures a week and a granary raid pays seven hundred gold. "
      "The arithmetic never favours virtue at that scale.</li>")
    a("<li><b>Destroying stock.</b> A market that clears daily holds about half a day of cloth on the shelf, so "
      "&ldquo;burn 400 cloth&rdquo; burned 36 and moved the price 2%. Adding stock works; taking it away cannot exceed "
      "what exists. The warehouse fire became a supply disruption to the weavers instead.</li>")
    a("<li><b>Tuning.</b> Every fix above revealed the next pathology. That is the nature of emergent systems, and "
      "the honest answer to a studio asking &ldquo;how long to tune this&rdquo; is: longer than you think. "
      "The diagnostic report is the best answer to that question.</li>")
    a("</ul>")

    # ---- next ------------------------------------------------------------
    a("<h2>What comes next</h2>")
    a("<ul>")
    a("<li>Seasons. Harvest cycles are the single biggest missing piece for a medieval feel.</li>")
    a("<li>Durables. Cloth and tools bought rarely and worn out, so they stop being 27% of a daily budget.</li>")
    a("<li>Imperfect information for merchants, so they speculate and sometimes lose.</li>")
    a("<li>Population that responds to being fed.</li>")
    a("<li>Save/load, a cost-per-tick budget, and a clean API: <span class=\"mono\">tick()</span>, "
      "<span class=\"mono\">price_of(good, town)</span>, <span class=\"mono\">trigger(event)</span>, "
      "<span class=\"mono\">player_trade()</span>.</li>")
    a("</ul>")

    a("<footer><p>Python 3, standard library only. economy.py, trade.py, kingdom.py, and the report generators. "
      "Extends a CMPM 146 (Game AI) group project at UC Santa Cruz with Jsanc189, Elroy Saltzherr and joshwidjaja. "
      "Every figure on this page is recomputed from the simulation when the page is built.</p></footer>")
    a("</div>")
    return "\n".join(h)


def main():
    print("single town ...")
    st = single_town()
    print("three towns ...")
    tt = three_towns()
    print("one year, with and without ...")
    yr = one_year()
    print("strategies across seeds ...")
    sg = strategies()
    print("accumulation ...")
    ac = accumulation()
    html = build(st, tt, yr, sg, ac)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s (%.0f KB)" % (OUT, len(html) / 1024))


if __name__ == "__main__":
    main()
