"""
Turn a finished simulation into a self-contained HTML page.

No dependencies and no external assets: the data is embedded as JSON and the
charts are drawn as SVG by a small script in the page itself. Open the file
straight from disk, or hand it to anyone.

Used by trade.py:  python trade.py --html report.html
"""

from __future__ import annotations

import json


PALETTE_NOTE = """Series colors are the validated categorical slots 1-4,
checked for colorblind separation against both the light and dark surface."""


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def build_payload(world, ticks, baseline=None):
    """Collect everything the page needs into one JSON-able dict."""
    towns = list(world.towns.values())
    town_keys = list(world.towns)
    goods = [g for g in towns[0].goods.values()]
    resources = list(towns[0].resources)

    lanes = {}
    for s in world.shipments:
        if s.sell_price <= 0:
            continue
        agg = lanes.setdefault((s.good, s.origin, s.destination),
                               {"trips": 0, "units": 0.0, "profit": 0.0})
        agg["trips"] += 1
        agg["units"] += s.qty
        agg["profit"] += s.profit

    integration = {}
    for g in goods:
        on = _mean(world.history["spread"][g.key][-100:])
        off = _mean(baseline.history["spread"][g.key][-100:]) if baseline else None
        integration[g.key] = {"with_trade": on, "without_trade": off}

    started = len(world.merchants) * world.trade_cfg.get("starting_gold", 0)
    ended = sum(m.gold for m in world.merchants)

    return {
        "ticks": ticks,
        "generated_note": PALETTE_NOTE,
        "events": [
            {"tick": t, "name": n, "flavor": f,
             "town": world.towns[o].name if o else None}
            for (t, n, f, o) in world.event_log
        ],
        "goods": [
            {"key": g.key, "name": g.name, "base_price": g.base_price,
             "resource": g.resource}
            for g in goods
        ],
        "resources": [
            {"key": r, "name": towns[0].resources[r]["name"]} for r in resources
        ],
        "towns": [
            {"key": k, "name": world.towns[k].name,
             "population": world.towns[k].population,
             "land": world.towns[k].total_land,
             "efficiency": world.towns[k].efficiency_bonus}
            for k in town_keys
        ],
        "prices": {
            g.key: {k: world.towns[k].history["price"][g.key] for k in town_keys}
            for g in goods
        },
        "available": {
            g.key: world.towns[town_keys[0]].history["available"][g.key]
            for g in goods
        },
        "allocation": {
            k: {r: world.towns[k].history["allocation"][r] for r in resources}
            for k in town_keys
        },
        "integration": integration,
        "lanes": [
            {"good": good, "origin": world.towns[a].name,
             "destination": world.towns[b].name,
             "trips": v["trips"], "units": v["units"], "profit": v["profit"]}
            for (good, a, b), v in sorted(lanes.items(),
                                          key=lambda kv: -kv[1]["units"])
        ],
        "final_prices": {
            g.key: {k: world.towns[k].goods[g.key].price for k in town_keys}
            for g in goods
        },
        "final_allocation": {
            k: {r: world.towns[k].history["allocation"][r][-1] for r in resources}
            for k in town_keys
        },
        "reputation": {k: world.towns[k].history["reputation"] for k in town_keys},
        "treasury": {k: world.towns[k].history["treasury"] for k in town_keys},
        "gold_by_home": {k: world.history["gold_by_home"][k] for k in town_keys},
        "prosperity": {
            k: world.towns[k].history["real_consumption"] for k in town_keys
        },
        "merchants": [
            {"name": m.name,
             "home": world.towns[m.home].name if m.home else "-",
             "home_key": m.home, "trips": m.trips, "gold": m.gold,
             "profit": m.profit, "by_good": m.by_good}
            for m in sorted(world.merchants, key=lambda m: -m.profit)
        ],
        "profit_by_good": {
            g.key: sum(m.by_good.get(g.key, 0.0) for m in world.merchants)
            for g in goods
        },
        "middlemen_take": sum(m.profit for m in world.merchants),
        "gross_imports": sum(world.imports.values()),
        "fleet": {
            "count": len(world.merchants),
            "shipments": sum(1 for s in world.shipments if s.sell_price > 0),
            "gold_start": started,
            "gold_end": ended,
        },
    }


def write(world, ticks, path, baseline=None):
    payload = build_payload(world, ticks, baseline)
    html = TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


TEMPLATE = r"""<title>Three Towns Exchange</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bitter:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&display=swap">
<style>
:root {
  color-scheme: light;
  --ground:      #f7f9fb;
  --surface:     #ffffff;
  --surface-2:   #eef3f7;
  --ink:         #0d1519;
  --ink-2:       #4d5c66;
  --ink-3:       #74858f;
  --rule:        #d9e3ea;
  --rule-soft:   #e9eff4;
  --accent:      #1f5f9e;
  --series-1:    #2a78d6;
  --series-2:    #eb6834;
  --series-3:    #1baf7a;
  --series-4:    #eda100;
  --shadow:      0 1px 2px rgba(13,21,25,.06), 0 8px 24px rgba(13,21,25,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground:    #0e1417;
    --surface:   #161e23;
    --surface-2: #1d272d;
    --ink:       #eef4f7;
    --ink-2:     #a3b4be;
    --ink-3:     #7d8e99;
    --rule:      #29353d;
    --rule-soft: #1f2930;
    --accent:    #6aa9e6;
    --series-1:  #3987e5;
    --series-2:  #d95926;
    --series-3:  #199e70;
    --series-4:  #c98500;
    --shadow:    0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground:    #0e1417;
  --surface:   #161e23;
  --surface-2: #1d272d;
  --ink:       #eef4f7;
  --ink-2:     #a3b4be;
  --ink-3:     #7d8e99;
  --rule:      #29353d;
  --rule-soft: #1f2930;
  --accent:    #6aa9e6;
  --series-1:  #3987e5;
  --series-2:  #d95926;
  --series-3:  #199e70;
  --series-4:  #c98500;
  --shadow:    0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: "Source Sans 3", ui-sans-serif, system-ui, sans-serif;
  font-size: 16px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 56px 24px 96px; }

h1, h2, h3 { font-family: Bitter, Georgia, serif; text-wrap: balance; margin: 0; }
h1 { font-size: clamp(30px, 4.5vw, 44px); font-weight: 700; letter-spacing: -.015em; }
h2 { font-size: 22px; font-weight: 600; letter-spacing: -.005em; }
h3 { font-size: 15px; font-weight: 600; }

.eyebrow {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--ink-3);
}
.lede { color: var(--ink-2); max-width: 66ch; font-size: 17px; margin-top: 14px; }

header { border-bottom: 1px solid var(--rule); padding-bottom: 34px; }
section { margin-top: 56px; }
.section-head { display: flex; flex-direction: column; gap: 6px; margin-bottom: 22px; }
.section-head p { margin: 0; color: var(--ink-2); max-width: 68ch; }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin-top: 30px; }
.tile { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 16px 18px; box-shadow: var(--shadow); }
.tile .n { font-family: Bitter, Georgia, serif; font-size: 30px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.tile .k { font-size: 13px; color: var(--ink-2); margin-top: 4px; }

.grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }
.card { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 18px 18px 12px; box-shadow: var(--shadow); }
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 2px; }
.card-note { font-size: 13px; color: var(--ink-3); }

.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 6px 0 10px; font-size: 13px; color: var(--ink-2); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 11px; height: 11px; border-radius: 3px; flex: none; }

figure { margin: 0; position: relative; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
.tip {
  position: absolute; pointer-events: none; opacity: 0; transition: opacity .1s;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  box-shadow: var(--shadow); padding: 9px 11px; font-size: 13px; min-width: 132px; z-index: 5;
}
.tip .t-head { font-family: "IBM Plex Mono", monospace; font-size: 11px; color: var(--ink-3); letter-spacing: .08em; text-transform: uppercase; margin-bottom: 6px; }
.tip .t-row { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.tip .t-row b { font-family: "IBM Plex Mono", monospace; font-weight: 500; font-variant-numeric: tabular-nums; }

.timeline { display: flex; flex-direction: column; gap: 0; }
.ev { display: grid; grid-template-columns: 74px 1fr; gap: 18px; padding: 15px 0; border-top: 1px solid var(--rule-soft); }
.ev:first-child { border-top: 0; }
.ev .when { font-family: "IBM Plex Mono", monospace; font-size: 13px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.ev .what { font-weight: 600; }
.ev .where { display: inline-block; margin-left: 8px; font-size: 11px; font-family: "IBM Plex Mono", monospace; letter-spacing: .06em; text-transform: uppercase; color: var(--ink-3); border: 1px solid var(--rule); border-radius: 999px; padding: 1px 8px; vertical-align: 1px; }
.ev .why { color: var(--ink-2); font-size: 15px; margin-top: 2px; }

.tablewrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 10px; background: var(--surface); box-shadow: var(--shadow); }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { padding: 10px 14px; text-align: right; border-bottom: 1px solid var(--rule-soft); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
thead th { font-family: "IBM Plex Mono", monospace; font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); font-weight: 500; }
tbody tr:last-child td { border-bottom: 0; }
td.num { font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums; }
.mark { display: inline-flex; align-items: center; gap: 7px; }

footer { margin-top: 64px; padding-top: 22px; border-top: 1px solid var(--rule); color: var(--ink-3); font-size: 13px; }
a { color: var(--accent); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>

<div class="wrap">
<header>
  <div class="eyebrow">Genetic-algorithm economy &middot; CMPM 146</div>
  <h1>Three Towns Exchange</h1>
  <p class="lede">
    Three towns with different land, one shared market, and no price table anywhere
    in the code. Prices here are state that carries from day to day, producers
    re-plant to chase profit, and caravans haul goods toward whoever is paying most.
    Everything below is what fell out of running that for <b id="tickcount">240</b> days.
  </p>
  <div class="tiles" id="tiles"></div>
</header>

<section>
  <div class="section-head">
    <h2>What happened to this region</h2>
    <p>Three shocks, none of which adjust a price directly. Each one changes a
      physical fact &mdash; how much a field yields, what can be mined, what the
      crown is buying &mdash; and lets the market work out the consequences.</p>
  </div>
  <div class="timeline" id="timeline"></div>
</section>

<section>
  <div class="section-head">
    <h2>Prices, town by town</h2>
    <p>One chart per good; one line per town. Dashed vertical rules mark the events
      above, and the faint horizontal rule is the good's starting price.</p>
  </div>
  <div class="grid-2" id="pricecharts"></div>
</section>

<section>
  <div class="section-head">
    <h2>Do the merchants actually do anything?</h2>
    <p>The gap between the cheapest and dearest town, as a share of the average
      price. A single integrated market would sit near zero. Same world, same seed,
      run twice &mdash; once with the caravans, once with them deleted.</p>
  </div>
  <div class="card">
    <div class="card-head"><h3>Average price gap between towns, last 100 days</h3></div>
    <figure id="integration"></figure>
  </div>
</section>

<section>
  <div class="section-head">
    <h2>What each town chose to grow</h2>
    <p>Land use is the genome. Nobody assigns these shares &mdash; producers that
      guessed profitably get bred, and the mix drifts toward whatever the prices
      are currently rewarding.</p>
  </div>
  <div class="grid-2" id="landcharts"></div>
</section>

<section>
  <div class="section-head">
    <h2>Who is winning</h2>
    <p>Reputation is a relative index &mdash; 50 is par, and the three towns are
      scored against each other on trade share, how well fed their people are, and
      what sits in the treasury. Nobody raises an army; they compete for standing,
      so one town can only climb by another slipping.</p>
  </div>
  <div class="grid-2">
    <div class="card">
      <div class="card-head"><h3>Reputation</h3><div class="card-note">50 = par</div></div>
      <div class="legend" id="rep-legend"></div>
      <figure id="reputation"></figure>
    </div>
    <div class="card">
      <div class="card-head"><h3>Merchant capital by home town</h3><div class="card-note">gold held by traders from each town</div></div>
      <div class="legend" id="gold-legend"></div>
      <figure id="goldbyhome"></figure>
    </div>
  </div>
</section>

<section>
  <div class="section-head">
    <h2>The merchants</h2>
    <p>Every trader, ranked by what they cleared over the whole run and coloured by
      the town they set out from. What separates them is not where they are from
      &mdash; it is how many trips they made and whether they got into the
      high-value cargo early.</p>
  </div>
  <div class="card">
    <div class="card-head"><h3>Profit per merchant</h3><div class="card-note">bar colour = home town</div></div>
    <div class="legend" id="merch-legend"></div>
    <figure id="merchants"></figure>
  </div>
  <div class="grid-2" style="margin-top:16px">
    <div class="card">
      <div class="card-head"><h3>Which cargo made the money</h3></div>
      <figure id="profitbygood"></figure>
    </div>
    <div class="card">
      <div class="card-head"><h3>Most profitable routes</h3></div>
      <figure id="routeprofit"></figure>
    </div>
  </div>
  <div class="tablewrap" style="margin-top:16px"><table id="merchtable"></table></div>
</section>

<section>
  <div class="section-head">
    <h2>Where the caravans went</h2>
    <p>Every delivered shipment, grouped by route. Merchants may only buy a good
      in a town that actually produces it, which is why each lane runs one way
      out of its source.</p>
  </div>
  <div class="card">
    <div class="card-head"><h3>Units delivered per route</h3></div>
    <figure id="lanes"></figure>
  </div>
</section>

<section>
  <div class="section-head">
    <h2>Where it all ended up</h2>
    <p>Final prices, land use and standings, as numbers.</p>
  </div>
  <div class="tablewrap"><table id="finaltable"></table></div>
  <div class="tablewrap" style="margin-top:16px"><table id="landtable"></table></div>
</section>

<footer>
  <p id="colophon"></p>
</footer>
</div>

<script>
const DATA = __PAYLOAD__;
const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const ARROW = "→";
const DOT = "·";
const fmt = (n, d) => n.toLocaleString(undefined, {minimumFractionDigits: d ?? 0, maximumFractionDigits: d ?? 0});
const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
const svgEl = (tag, attrs) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
};
const goodName = k => (DATA.goods.find(g => g.key === k) || {}).name || k;

/* ---------- summary tiles ---------- */
(function tiles() {
  document.getElementById("tickcount").textContent = DATA.ticks;
  const wheat = DATA.integration.wheat;
  const drop = wheat.without_trade ? (1 - wheat.with_trade / wheat.without_trade) * 100 : 0;
  const take = DATA.gross_imports ? DATA.middlemen_take / DATA.gross_imports * 100 : 0;
  const items = [
    [DATA.towns.length + " towns", "each with its own land, prices and stock"],
    [fmt(DATA.fleet.shipments), "caravan deliveries between them"],
    [fmt(drop) + "%", "smaller wheat price gap once merchants exist"],
    [fmt(take) + "%", "of turnover kept by the middlemen"],
  ];
  const host = document.getElementById("tiles");
  for (const [n, k] of items) {
    const t = el("div", "tile");
    const a = el("div", "n"); a.textContent = n;
    const b = el("div", "k"); b.textContent = k;
    t.append(a, b); host.append(t);
  }
})();

/* ---------- event timeline ---------- */
(function timeline() {
  const host = document.getElementById("timeline");
  DATA.events.forEach(e => {
    const row = el("div", "ev");
    const when = el("div", "when"); when.textContent = "Day " + e.tick;
    const body = el("div");
    const what = el("div", "what"); what.textContent = e.name;
    const w = el("span", "where");
    w.textContent = e.town || "region-wide";
    what.append(w);
    const why = el("div", "why"); why.textContent = e.flavor;
    body.append(what, why);
    row.append(when, body); host.append(row);
  });
})();

/* ---------- shared chart plumbing ---------- */
const W = 640, H = 250, M = {t: 14, r: 14, b: 26, l: 46};

function makeTip(fig) {
  const tip = el("div", "tip");
  fig.append(tip);
  return tip;
}

function placeTip(fig, tip, ev) {
  const box = fig.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(ev.clientX - box.left + 14, 0),
                            Math.max(0, box.width - tip.offsetWidth - 2)) + "px";
  tip.style.top = Math.max(0, ev.clientY - box.top - tip.offsetHeight - 12) + "px";
}

function axisText(x, y, str, anchor) {
  const t = svgEl("text", {x, y, "text-anchor": anchor || "middle"});
  t.setAttribute("font-size", "11");
  t.setAttribute("font-family", "IBM Plex Mono, monospace");
  t.style.fill = "var(--ink-3)";
  t.textContent = str;
  return t;
}

function lineChart(fig, opts) {
  const {series, events, baseline, ticks, unit} = opts;
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img"});
  const tip = makeTip(fig);

  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const v of s.values) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (baseline != null) { lo = Math.min(lo, baseline); hi = Math.max(hi, baseline); }
  if (!(hi > lo)) hi = lo + 1;
  const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;

  const px = i => M.l + i / Math.max(ticks - 1, 1) * (W - M.l - M.r);
  const py = v => M.t + (1 - (v - lo) / (hi - lo)) * (H - M.t - M.b);

  for (let g = 0; g <= 3; g++) {
    const v = lo + (hi - lo) * g / 3;
    const y = py(v);
    const ln = svgEl("line", {x1: M.l, x2: W - M.r, y1: y, y2: y, "stroke-width": 1});
    ln.style.stroke = "var(--rule-soft)";
    svg.append(ln);
    svg.append(axisText(M.l - 8, y + 4, fmt(v, Math.abs(v) < 20 ? 1 : 0), "end"));
  }

  if (baseline != null) {
    const y = py(baseline);
    const ln = svgEl("line", {x1: M.l, x2: W - M.r, y1: y, y2: y, "stroke-width": 1, "stroke-dasharray": "1 4"});
    ln.style.stroke = "var(--ink-3)";
    svg.append(ln);
  }

  for (const e of (events || [])) {
    const x = px(e.tick);
    const ln = svgEl("line", {x1: x, x2: x, y1: M.t, y2: H - M.b, "stroke-width": 1, "stroke-dasharray": "3 3"});
    ln.style.stroke = "var(--ink-3)";
    ln.style.opacity = ".55";
    svg.append(ln);
  }

  series.forEach(s => {
    const start = s.from || 0;
    let d = "";
    for (let k = start; k < s.values.length; k++) {
      d += (k === start ? "M" : "L") + px(k).toFixed(1) + " " + py(s.values[k]).toFixed(1);
    }
    const path = svgEl("path", {d, fill: "none", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"});
    path.style.stroke = s.color;
    svg.append(path);
  });

  svg.append(axisText(M.l, H - 8, "day 0", "start"));
  svg.append(axisText(W - M.r, H - 8, "day " + ticks, "end"));

  const cross = svgEl("line", {y1: M.t, y2: H - M.b, "stroke-width": 1});
  cross.style.stroke = "var(--ink-3)";
  cross.style.opacity = "0";
  svg.append(cross);
  const dots = series.map(s => {
    const c = svgEl("circle", {r: 4, "stroke-width": 2});
    c.style.fill = s.color;
    c.style.stroke = "var(--surface)";
    c.style.opacity = "0";
    svg.append(c);
    return c;
  });

  svg.append(svgEl("rect", {x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent"}));

  svg.addEventListener("pointermove", ev => {
    const box = svg.getBoundingClientRect();
    const sx = (ev.clientX - box.left) / box.width * W;
    let i = Math.round((sx - M.l) / (W - M.l - M.r) * (ticks - 1));
    i = Math.max(0, Math.min(ticks - 1, i));
    cross.setAttribute("x1", px(i)); cross.setAttribute("x2", px(i));
    cross.style.opacity = ".45";
    let rows = "";
    series.forEach((s, k) => {
      const live = i >= (s.from || 0);
      dots[k].style.opacity = live ? "1" : "0";
      if (live) { dots[k].setAttribute("cx", px(i)); dots[k].setAttribute("cy", py(s.values[i])); }
      rows += `<div class="t-row"><span class="mark"><span class="swatch" style="background:${s.color}"></span>${s.name}</span><b>${live ? (unit || "") + fmt(s.values[i], 2) : "&mdash;"}</b></div>`;
    });
    tip.innerHTML = `<div class="t-head">Day ${i}</div>` + rows;
    tip.style.opacity = "1";
    placeTip(fig, tip, ev);
  });
  svg.addEventListener("pointerleave", () => {
    tip.style.opacity = "0"; cross.style.opacity = "0";
    dots.forEach(d => d.style.opacity = "0");
  });
  fig.prepend(svg);
}

function stackedArea(fig, opts) {
  const {bands, events, ticks} = opts;
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img"});
  const tip = makeTip(fig);
  const px = i => M.l + i / Math.max(ticks - 1, 1) * (W - M.l - M.r);
  const py = v => M.t + (1 - v) * (H - M.t - M.b);

  const cum = new Array(ticks).fill(0);
  bands.forEach(band => {
    const top = [], bottom = [];
    for (let i = 0; i < ticks; i++) {
      const total = bands.reduce((a, b) => a + b.values[i], 0) || 1;
      bottom.push(cum[i]);
      cum[i] += band.values[i] / total;
      top.push(cum[i]);
    }
    let d = "";
    for (let i = 0; i < ticks; i++) d += (i ? "L" : "M") + px(i).toFixed(1) + " " + py(top[i]).toFixed(1);
    for (let i = ticks - 1; i >= 0; i--) d += "L" + px(i).toFixed(1) + " " + py(bottom[i]).toFixed(1);
    d += "Z";
    const p = svgEl("path", {d, "stroke-width": 2});
    p.style.fill = band.color;
    p.style.stroke = "var(--surface)";
    svg.append(p);
  });

  for (let g = 0; g <= 4; g++) svg.append(axisText(M.l - 8, py(g / 4) + 4, (g * 25) + "%", "end"));
  for (const e of (events || [])) {
    const x = px(e.tick);
    const ln = svgEl("line", {x1: x, x2: x, y1: M.t, y2: H - M.b, "stroke-width": 1, "stroke-dasharray": "3 3"});
    ln.style.stroke = "var(--ink)";
    ln.style.opacity = ".5";
    svg.append(ln);
  }
  svg.append(axisText(M.l, H - 8, "day 0", "start"));
  svg.append(axisText(W - M.r, H - 8, "day " + ticks, "end"));

  const cross = svgEl("line", {y1: M.t, y2: H - M.b, "stroke-width": 1});
  cross.style.stroke = "var(--ink)"; cross.style.opacity = "0";
  svg.append(cross);
  svg.append(svgEl("rect", {x: M.l, y: M.t, width: W - M.l - M.r, height: H - M.t - M.b, fill: "transparent"}));

  svg.addEventListener("pointermove", ev => {
    const box = svg.getBoundingClientRect();
    const sx = (ev.clientX - box.left) / box.width * W;
    let i = Math.round((sx - M.l) / (W - M.l - M.r) * (ticks - 1));
    i = Math.max(0, Math.min(ticks - 1, i));
    cross.setAttribute("x1", px(i)); cross.setAttribute("x2", px(i));
    cross.style.opacity = ".5";
    const total = bands.reduce((a, b) => a + b.values[i], 0) || 1;
    let rows = "";
    for (const b of bands) {
      rows += `<div class="t-row"><span class="mark"><span class="swatch" style="background:${b.color}"></span>${b.name}</span><b>${fmt(b.values[i] / total * 100, 0)}%</b></div>`;
    }
    tip.innerHTML = `<div class="t-head">Day ${i}</div>` + rows;
    tip.style.opacity = "1";
    placeTip(fig, tip, ev);
  });
  svg.addEventListener("pointerleave", () => { tip.style.opacity = "0"; cross.style.opacity = "0"; });
  fig.prepend(svg);
}

function hBars(fig, opts) {
  const {rows, labelWidth, unit, height} = opts;
  const BW = 640, rowH = height || 24, m = {t: 6, r: 68, b: 6, l: labelWidth || 200};
  const BH = m.t + m.b + rows.length * rowH;
  const svg = svgEl("svg", {viewBox: `0 0 ${BW} ${BH}`, role: "img"});
  const tip = makeTip(fig);
  const max = Math.max(...rows.map(r => Math.abs(r.value))) * 1.05 || 1;

  rows.forEach((r, i) => {
    const y = m.t + i * rowH;
    const w = (Math.abs(r.value) / max) * (BW - m.l - m.r);
    const bar = svgEl("rect", {x: m.l, y: y + 4, width: Math.max(2, w), height: rowH - 10, rx: 4});
    bar.style.fill = r.color;
    bar.addEventListener("pointerenter", ev => {
      tip.innerHTML = `<div class="t-head">${r.label}</div>` + (r.detail || "");
      tip.style.opacity = "1";
      placeTip(fig, tip, ev);
    });
    bar.addEventListener("pointerleave", () => tip.style.opacity = "0");
    svg.append(bar);

    const lab = axisText(m.l - 10, y + rowH / 2 + 4, r.label, "end");
    lab.setAttribute("font-family", "Source Sans 3, sans-serif");
    lab.setAttribute("font-size", "12.5");
    lab.style.fill = "var(--ink-2)";
    svg.append(lab);
    svg.append(axisText(m.l + Math.max(2, w) + 8, y + rowH / 2 + 4,
                        fmt(r.value) + (unit || ""), "start"));
  });
  fig.prepend(svg);
}

function legend(host, items) {
  const l = el("div", "legend");
  for (const it of items) {
    const s = el("span");
    const sw = el("span", "swatch"); sw.style.background = it.color;
    s.append(sw, document.createTextNode(it.name));
    l.append(s);
  }
  host.append(l);
}

/* ---------- price charts ---------- */
(function priceCharts() {
  const host = document.getElementById("pricecharts");
  const townColors = DATA.towns.map((t, i) => ({...t, color: SERIES[i]}));
  DATA.goods.forEach(good => {
    const card = el("div", "card");
    const head = el("div", "card-head");
    const h = el("h3"); h.textContent = good.name;
    const note = el("div", "card-note"); note.textContent = "starts at " + good.base_price.toFixed(2) + "g";
    head.append(h, note);
    card.append(head);
    legend(card, townColors);
    const fig = el("figure");
    card.append(fig);
    host.append(card);

    const avail = DATA.available[good.key];
    let from = avail.findIndex(Boolean);
    if (from < 0) from = 0;
    lineChart(fig, {
      ticks: DATA.ticks,
      baseline: good.base_price,
      events: DATA.events,
      series: townColors.map(t => ({
        name: t.name, color: t.color, from,
        values: DATA.prices[good.key][t.key],
      })),
    });
  });
})();

/* ---------- integration bars ---------- */
(function integration() {
  const fig = document.getElementById("integration");
  const goods = DATA.goods;
  const IW = 640, IH = 210, m = {t: 12, r: 14, b: 44, l: 46};
  const svg = svgEl("svg", {viewBox: `0 0 ${IW} ${IH}`, role: "img"});
  const tip = makeTip(fig);
  const max = Math.max(...goods.map(g => Math.max(
    DATA.integration[g.key].without_trade || 0, DATA.integration[g.key].with_trade || 0))) * 1.15;
  const py = v => m.t + (1 - v / max) * (IH - m.t - m.b);
  const bandW = (IW - m.l - m.r) / goods.length;

  for (let g = 0; g <= 3; g++) {
    const v = max * g / 3, y = py(v);
    const ln = svgEl("line", {x1: m.l, x2: IW - m.r, y1: y, y2: y, "stroke-width": 1});
    ln.style.stroke = "var(--rule-soft)"; svg.append(ln);
    svg.append(axisText(m.l - 8, y + 4, fmt(v) + "%", "end"));
  }

  goods.forEach((g, i) => {
    const cx = m.l + bandW * (i + 0.5);
    const pair = [
      {label: "without merchants", v: DATA.integration[g.key].without_trade || 0, fill: "var(--ink-3)"},
      {label: "with merchants", v: DATA.integration[g.key].with_trade || 0, fill: "var(--series-1)"},
    ];
    pair.forEach((p, k) => {
      const w = Math.min(34, bandW * 0.32);
      const x = cx + (k ? 3 : -w - 3);
      const y = py(p.v);
      const r = svgEl("rect", {x, y, width: w, height: Math.max(1, IH - m.b - y), rx: 4});
      r.style.fill = p.fill;
      r.addEventListener("pointerenter", ev => {
        tip.innerHTML = `<div class="t-head">${g.name}</div><div class="t-row"><span>${p.label}</span><b>${fmt(p.v)}%</b></div>`;
        tip.style.opacity = "1";
        placeTip(fig, tip, ev);
      });
      r.addEventListener("pointerleave", () => tip.style.opacity = "0");
      svg.append(r);
      const lbl = axisText(x + w / 2, y - 6, fmt(p.v) + "%");
      lbl.style.fill = "var(--ink-2)";
      svg.append(lbl);
    });
    const name = axisText(cx, IH - 24, g.name);
    name.setAttribute("font-family", "Source Sans 3, sans-serif");
    name.setAttribute("font-size", "12");
    name.style.fill = "var(--ink-2)";
    svg.append(name);
  });

  svg.append(axisText(m.l, IH - 6,
    "grey = merchants deleted   " + DOT + "   blue = merchants running", "start"));
  fig.prepend(svg);
})();

/* ---------- land use ---------- */
(function land() {
  const host = document.getElementById("landcharts");
  const bandsMeta = DATA.resources.map((r, i) => ({...r, color: SERIES[i]}));
  DATA.towns.forEach(town => {
    const card = el("div", "card");
    const head = el("div", "card-head");
    const h = el("h3"); h.textContent = town.name;
    const best = Object.entries(town.efficiency).sort((a, b) => b[1] - a[1])[0];
    const bestName = (DATA.resources.find(r => r.key === best[0]) || {}).name || best[0];
    const note = el("div", "card-note");
    note.textContent = "best land: " + bestName.toLowerCase() + " x" + best[1].toFixed(2);
    head.append(h, note);
    card.append(head);
    legend(card, bandsMeta);
    const fig = el("figure"); card.append(fig); host.append(card);
    stackedArea(fig, {
      ticks: DATA.ticks,
      events: DATA.events,
      bands: bandsMeta.map(b => ({name: b.name, color: b.color, values: DATA.allocation[town.key][b.key]})),
    });
  });
})();

/* ---------- standings: reputation and merchant capital ---------- */
(function standings() {
  const townColors = DATA.towns.map((t, i) => ({...t, color: SERIES[i]}));
  legend(document.getElementById("rep-legend"), townColors);
  legend(document.getElementById("gold-legend"), townColors);
  lineChart(document.getElementById("reputation"), {
    ticks: DATA.ticks, events: DATA.events, baseline: 50,
    series: townColors.map(t => ({name: t.name, color: t.color, values: DATA.reputation[t.key]})),
  });
  lineChart(document.getElementById("goldbyhome"), {
    ticks: DATA.ticks, events: DATA.events,
    series: townColors.map(t => ({name: t.name, color: t.color, values: DATA.gold_by_home[t.key]})),
  });
})();

/* ---------- merchants ---------- */
(function merchants() {
  const homeColor = {};
  DATA.towns.forEach((t, i) => homeColor[t.key] = SERIES[i]);
  legend(document.getElementById("merch-legend"),
         DATA.towns.map((t, i) => ({name: t.name, color: SERIES[i]})));

  hBars(document.getElementById("merchants"), {
    labelWidth: 170, unit: "g", height: 22,
    rows: DATA.merchants.map(m => {
      const top = Object.entries(m.by_good).sort((a, b) => b[1] - a[1]).slice(0, 3);
      const detail = top.map(([k, v]) =>
        `<div class="t-row"><span>${goodName(k)}</span><b>${fmt(v)}g</b></div>`).join("")
        + `<div class="t-row"><span>trips</span><b>${m.trips}</b></div>`;
      return {label: m.name + " " + DOT + " " + m.home, value: m.profit,
              color: homeColor[m.home_key] || SERIES[0], detail};
    }),
  });

  hBars(document.getElementById("profitbygood"), {
    labelWidth: 120, unit: "g", height: 30,
    rows: DATA.goods.map((g, i) => ({
      label: g.name, value: DATA.profit_by_good[g.key] || 0, color: SERIES[i],
      detail: `<div class="t-row"><span>merchant profit</span><b>${fmt(DATA.profit_by_good[g.key] || 0)}g</b></div>`,
    })).sort((a, b) => b.value - a.value),
  });

  const goodIdx = {};
  DATA.goods.forEach((g, i) => goodIdx[g.key] = i);
  hBars(document.getElementById("routeprofit"), {
    labelWidth: 210, unit: "g", height: 26,
    rows: DATA.lanes.slice().sort((a, b) => b.profit - a.profit).slice(0, 6).map(r => ({
      label: goodName(r.good) + ": " + r.origin + " " + ARROW + " " + r.destination,
      value: r.profit, color: SERIES[goodIdx[r.good] % SERIES.length],
      detail: `<div class="t-row"><span>trips</span><b>${r.trips}</b></div>`
            + `<div class="t-row"><span>units</span><b>${fmt(r.units)}</b></div>`,
    })),
  });

  let h = "<thead><tr><th>Merchant</th><th>Home</th><th>Trips</th><th>Gold</th><th>Profit</th><th>Best cargo</th></tr></thead><tbody>";
  DATA.merchants.forEach(m => {
    const top = Object.entries(m.by_good).sort((a, b) => b[1] - a[1])[0];
    h += `<tr><td><span class="mark"><span class="swatch" style="background:${homeColor[m.home_key]}"></span>${m.name}</span></td>`
      + `<td>${m.home}</td><td class="num">${m.trips}</td>`
      + `<td class="num">${fmt(m.gold)}g</td><td class="num">${fmt(m.profit)}g</td>`
      + `<td>${top ? goodName(top[0]) + " (" + fmt(top[1] / Math.max(m.profit, 1) * 100) + "%)" : "&mdash;"}</td></tr>`;
  });
  document.getElementById("merchtable").innerHTML = h + "</tbody>";
})();

/* ---------- trade lanes ---------- */
(function lanes() {
  const goodIdx = {};
  DATA.goods.forEach((g, i) => goodIdx[g.key] = i);
  hBars(document.getElementById("lanes"), {
    labelWidth: 210, height: 26,
    rows: DATA.lanes.slice(0, 12).map(r => ({
      label: goodName(r.good) + ": " + r.origin + " " + ARROW + " " + r.destination,
      value: r.units, color: SERIES[goodIdx[r.good] % SERIES.length],
      detail: `<div class="t-row"><span>trips</span><b>${r.trips}</b></div>`
            + `<div class="t-row"><span>profit</span><b>${fmt(r.profit)}g</b></div>`,
    })),
  });
})();

/* ---------- tables (also the accessible fallback for the charts) ---------- */
(function tables() {
  const t1 = document.getElementById("finaltable");
  let h = "<thead><tr><th>Good</th><th>Start</th>";
  DATA.towns.forEach(t => h += `<th>${t.name}</th>`);
  h += "<th>Gap</th></tr></thead><tbody>";
  DATA.goods.forEach((g, i) => {
    const ps = DATA.towns.map(t => DATA.final_prices[g.key][t.key]);
    const mean = ps.reduce((a, b) => a + b, 0) / ps.length;
    const gap = (Math.max(...ps) - Math.min(...ps)) / mean * 100;
    h += `<tr><td><span class="mark"><span class="swatch" style="background:${SERIES[i]}"></span>${g.name}</span></td>`;
    h += `<td class="num">${g.base_price.toFixed(2)}g</td>`;
    ps.forEach(p => h += `<td class="num">${p.toFixed(2)}g</td>`);
    h += `<td class="num">${fmt(gap)}%</td></tr>`;
  });
  t1.innerHTML = h + "</tbody>";

  const t2 = document.getElementById("landtable");
  let k = "<thead><tr><th>Town</th>";
  DATA.resources.forEach(r => k += `<th>${r.name}</th>`);
  k += "<th>Reputation</th><th>Treasury</th></tr></thead><tbody>";
  DATA.towns.forEach(t => {
    k += `<tr><td>${t.name}</td>`;
    DATA.resources.forEach(r => k += `<td class="num">${fmt(DATA.final_allocation[t.key][r.key] * 100)}%</td>`);
    const rep = DATA.reputation[t.key], tre = DATA.treasury[t.key];
    k += `<td class="num">${fmt(rep[rep.length - 1], 1)}</td>`;
    k += `<td class="num">${fmt(tre[tre.length - 1])}g</td></tr>`;
  });
  t2.innerHTML = k + "</tbody>";

  document.getElementById("colophon").textContent =
    DATA.fleet.count + " merchants " + DOT + " " + DATA.ticks + " simulated days " + DOT + " "
    + "generated by trade.py, no plotting library. " + DATA.generated_note.replace(/\s+/g, " ");
})();
</script>
"""
