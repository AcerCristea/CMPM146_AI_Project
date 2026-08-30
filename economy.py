"""
A dynamic economy for games: prices that move because the world moved.

The problem with most game economies is that they are lookup tables. A sword
costs 150 gold on day 1 and on day 400, no matter what happened in between.

This simulation makes price a piece of *persistent state* that chases supply
and demand, and it makes producers adapt to that price with a genetic
algorithm running continuously in the background. That second part is the
important one: the GA is not searching for "the best economy" once and then
stopping. It is the mechanism by which the world re-equilibrates after a
shock. A blight kills the grain harvest, wheat gets scarce, wheat price
climbs, farmers chase the profit and move land into grain, supply recovers,
price settles somewhere new. Nobody scripted that curve.

It also means the economy self-corrects instead of collapsing into a
monoculture: when every producer piles into wheat, wheat's price falls and
wheat stops being the profitable choice.

Events are the hook for gameplay. Anything the player does that should move
the market -- discovering a better ore, burning a granary, starting a war --
is a call to Economy.trigger(). Scheduled events in world.json go through
that exact same method, so a scripted famine and a player-caused one are the
same thing as far as the market is concerned.

Stdlib only. Run with:  python economy.py
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
from dataclasses import dataclass


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def softmax(logits):
    """Turn unconstrained numbers into shares that sum to 1.

    This is what keeps the genome valid without a normalize() step, and it is
    why a land use that has dropped to ~0% can climb back later: mutation is
    additive in logit space, so nothing ever gets stuck at zero.
    """
    peak = max(logits.values())
    exp = {k: math.exp(v - peak) for k, v in logits.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}


# --------------------------------------------------------------------------
# world model
# --------------------------------------------------------------------------

@dataclass
class Good:
    key: str
    name: str
    resource: str
    base_price: float
    yield_per_land: float
    base_need: float        # units wanted per person per tick at base price
    elasticity: float       # how hard demand responds to price
    utility: float          # priority in the household budget
    spoilage: float         # fraction of unsold stock lost each tick
    available: bool = True

    price: float = 0.0
    inventory: float = 0.0
    need_scale: float = 1.0   # moved by discoveries (substitution)

    def __post_init__(self):
        if self.price == 0.0:
            self.price = self.base_price


@dataclass
class Modifier:
    """An active effect on the world, created by an event."""
    kind: str               # "efficiency" | "demand"
    target: str             # resource key or good key
    value: float
    start: int
    duration: int
    recovery: str = "none"  # "linear" fades the effect back to neutral
    label: str = ""

    def strength_at(self, tick):
        """Current multiplier, or None once the modifier has expired."""
        elapsed = tick - self.start
        if elapsed < 0 or elapsed > self.duration:
            return None
        if self.recovery == "linear" and self.duration > 0:
            progress = elapsed / self.duration
            return self.value + (1.0 - self.value) * progress
        return self.value


@dataclass
class Producer:
    """One farm/mine. Its genome is a land allocation over unlocked resources."""
    logits: dict
    fitness: float = 0.0
    last_revenue: float = 0.0

    def allocation(self):
        return softmax(self.logits)


# --------------------------------------------------------------------------
# the simulation
# --------------------------------------------------------------------------

class Town:
    """One local market: its own land, producers, prices and stock.

    A town knows nothing about other towns. Everything that couples them --
    price convergence, shortages spreading, gluts draining away -- comes from
    merchants moving goods, in trade.py.
    """

    def __init__(self, config, seed=None, name=None, population=None,
                 total_land=None, efficiency=None):
        self.cfg = copy.deepcopy(config)
        sim = self.cfg["simulation"]
        self.market = self.cfg["market"]

        self.rng = random.Random(sim["seed"] if seed is None else seed)
        self.name = name or "Market"
        self.population = population if population is not None else sim["population"]
        self.total_land = total_land if total_land is not None else sim["total_land"]

        # Static land quality. This is what gives each town a comparative
        # advantage, which is what creates a price gap worth arbitraging.
        self.efficiency_bonus = dict(efficiency or {})

        self.resources = {k: dict(v) for k, v in self.cfg["resources"].items()}
        self.goods = {}
        for key, spec in self.cfg["goods"].items():
            self.goods[key] = Good(
                key=key,
                name=spec["name"],
                resource=spec["resource"],
                base_price=spec["base_price"],
                yield_per_land=spec["yield_per_land"],
                base_need=spec["base_need"],
                elasticity=spec["elasticity"],
                utility=spec["utility"],
                spoilage=spec["spoilage"],
                available=spec.get("available", True),
            )

        self.modifiers = []
        self.discoveries = []       # in-progress adoption curves
        self.substitutes = {}       # good key -> the good that replaced it
        self.scheduled = sorted(self.cfg.get("events", []), key=lambda e: e["tick"])
        self.event_log = []

        # Demand from merchants buying for export, carried from last tick.
        # It feeds the price signal, which is how a shortage in one town
        # makes itself felt in the towns that supply it.
        self.merchant_demand = {k: 0.0 for k in self.goods}
        # Coin in the town coffers, from taxing trade that happens here
        # and from tribute its own merchants send home.
        self.treasury = 0.0
        # Standing among the three towns. 50 is par; it is relative, so
        # one town can only climb by another slipping.
        self.reputation = 50.0
        # last tick's total supply per good, used to size export bids
        self.last_supply = {k: 0.0 for k in self.goods}
        # what this town actually made last tick, as opposed to what it
        # merely has in stock because a caravan dropped it off
        self.last_production = {k: 0.0 for k in self.goods}

        # producers start with a mild random spread over unlocked land
        n = sim["producers"]
        self.land_per_producer = self.total_land / n
        self.producers = [
            Producer(logits={r: self.rng.gauss(0.0, 0.5)
                             for r in self.unlocked_resources()})
            for _ in range(n)
        ]

        self.history = {
            "price": {k: [] for k in self.goods},
            "sold": {k: [] for k in self.goods},
            "inventory": {k: [] for k in self.goods},
            "allocation": {r: [] for r in self.resources},
            "available": {k: [] for k in self.goods},
            "real_consumption": [],
            "treasury": [],
            "reputation": [],
            "unmet": [],
        }

    # -- world queries -----------------------------------------------------

    def unlocked_resources(self):
        return [k for k, v in self.resources.items() if v.get("unlocked")]

    def active_goods(self):
        return [g for g in self.goods.values() if g.available]

    def efficiency_of(self, resource, tick):
        mult = self.efficiency_bonus.get(resource, 1.0)
        for m in self.modifiers:
            if m.kind == "efficiency" and m.target == resource:
                s = m.strength_at(tick)
                if s is not None:
                    mult *= s
        return mult

    def demand_scale_of(self, good_key, tick):
        mult = 1.0
        for m in self.modifiers:
            if m.kind == "demand" and m.target == good_key:
                s = m.strength_at(tick)
                if s is not None:
                    mult *= s
        return mult

    def income_at(self, tick):
        """Household spending power, which events can expand or contract.

        This matters more than it looks. With a fixed budget, boosting demand
        for one good necessarily starves every other good -- declare war and
        weapons get *cheaper*, because cloth ate the money. Real war economies
        spend more in total, so wartime events raise this too.
        """
        income = self.market["income_per_capita"]
        for m in self.modifiers:
            if m.kind == "income":
                s = m.strength_at(tick)
                if s is not None:
                    income *= s
        return income

    # -- the gameplay hook -------------------------------------------------

    def trigger(self, event, tick):
        """Fire an event into the world. THIS IS THE GAME-FACING API.

        Scheduled events from world.json and events caused by the player both
        arrive here, so there is no separate code path for "scripted" versus
        "emergent" change.
        """
        kind = event["type"]
        name = event.get("name", kind)

        if kind == "disaster":
            self.modifiers.append(Modifier(
                kind="efficiency",
                target=event["target"],
                value=event["efficiency_mult"],
                start=tick,
                duration=event.get("duration", 30),
                recovery=event.get("recovery", "none"),
                label=name,
            ))

        elif kind == "demand_shock":
            # Either one target, or a whole basket of them: a war wants
            # uniforms AND weapons, not uniforms at the expense of weapons.
            targets = event.get("targets")
            if targets is None:
                targets = {event["target"]: event["demand_mult"]}
            for good_key, mult in targets.items():
                self.modifiers.append(Modifier(
                    kind="demand",
                    target=good_key,
                    value=mult,
                    start=tick,
                    duration=event.get("duration", 30),
                    recovery=event.get("recovery", "none"),
                    label=name,
                ))
            if "income_mult" in event:
                self.modifiers.append(Modifier(
                    kind="income",
                    target="*",
                    value=event["income_mult"],
                    start=tick,
                    duration=event.get("duration", 30),
                    recovery=event.get("recovery", "none"),
                    label=name,
                ))

        elif kind == "discovery":
            # The deposit is in one place; the demand for what it makes is
            # everywhere. Towns without the resource still want the good, and
            # still stop wanting the thing it replaced -- they just have to
            # buy it from whoever is sitting on the vein.
            res = event.get("unlocks_resource")
            if res:
                self.resources[res]["unlocked"] = True
                # Every producer gains a gene for the new land use, starting
                # low so it must prove itself but stays reachable by mutation.
                floor = min(min(p.logits.values()) for p in self.producers)
                for p in self.producers:
                    p.logits[res] = floor - 1.0 + self.rng.gauss(0.0, 0.3)

            new_good = self.goods[event["introduces"]]
            new_good.available = True
            new_good.need_scale = 1.0

            if event.get("substitutes_for"):
                self.substitutes[event["substitutes_for"]] = event["introduces"]

            self.discoveries.append({
                "good": event["introduces"],
                "displaces": event.get("substitutes_for"),
                "capture": event.get("demand_capture", 0.0),
                "target_need": event.get("new_need", new_good.base_need),
                "ticks": max(1, event.get("adoption_ticks", 20)),
                "start": tick,
            })

        else:
            raise ValueError("unknown event type: %r" % kind)

        self.event_log.append((tick, name, event.get("flavor", "")))
        return name

    def _advance_discoveries(self, tick):
        """Adoption curves: a better good takes demand from the one it beats."""
        for d in self.discoveries:
            progress = clamp((tick - d["start"]) / d["ticks"], 0.0, 1.0)
            self.goods[d["good"]].base_need = d["target_need"] * progress
            if d["displaces"]:
                self.goods[d["displaces"]].need_scale = 1.0 - d["capture"] * progress

    # -- the economic tick -------------------------------------------------

    def _produce(self, tick):
        """Each producer turns its land allocation into goods."""
        output = {k: 0.0 for k in self.goods}
        per_producer = []
        goods_by_resource = {}
        for g in self.active_goods():
            goods_by_resource.setdefault(g.resource, []).append(g)

        for p in self.producers:
            mine = {}
            for res, share in p.allocation().items():
                eff = self.efficiency_of(res, tick)
                for g in goods_by_resource.get(res, []):
                    made = share * self.land_per_producer * g.yield_per_land * eff
                    mine[g.key] = mine.get(g.key, 0.0) + made
                    output[g.key] += made
            per_producer.append(mine)

        return output, per_producer

    def _substitution_factor(self, good):
        """How much demand a good keeps once something better exists.

        A discovery does not just permanently delete demand for the old good.
        Buyers compare value for money, so if mithril tools get scarce and
        expensive the iron trade comes back, and if iron gets greedy it dies
        faster. Without this the obsolete good can drift to a HIGHER price
        than its replacement, which no player would ever believe.
        """
        sub_key = self.substitutes.get(good.key)
        if not sub_key:
            return 1.0
        other = self.goods.get(sub_key)
        if other is None or not other.available:
            return 1.0

        mine = good.price / max(good.utility, 1e-6)
        theirs = other.price / max(other.utility, 1e-6)
        return clamp((theirs / max(mine, 1e-6)) ** 1.2, 0.15, 2.5)

    def _clear_market(self, production, tick):
        """Households spend a fixed income, buying by need priority.

        Ordering by utility (food before cloth before tools) is what makes a
        famine hurt across the whole market: when bread eats the budget there
        is nothing left for tools, so the tool price falls even though
        nothing happened to tools.
        """
        budget = self.income_at(tick) * self.population
        supply = {g.key: g.inventory + production.get(g.key, 0.0)
                  for g in self.goods.values()}

        order = sorted(self.active_goods(), key=lambda g: -g.utility)
        wanted, sold = {}, {}
        unmet_spend = 0.0

        for g in order:
            price_ratio = g.base_price / max(g.price, 1e-6)
            desired = (self.population
                       * g.base_need
                       * g.need_scale
                       * (price_ratio ** g.elasticity)
                       * self.demand_scale_of(g.key, tick))
            desired *= self._substitution_factor(g)

            affordable = budget / max(g.price, 1e-6)
            want = max(0.0, min(desired, affordable))
            wanted[g.key] = want
            budget -= want * g.price

            bought = min(want, supply[g.key])
            sold[g.key] = bought
            unmet_spend += (want - bought) * g.price

        for g in self.goods.values():
            if g.key not in sold:
                wanted[g.key], sold[g.key] = 0.0, 0.0
            leftover = supply[g.key] - sold[g.key]
            g.inventory = max(0.0, leftover * (1.0 - g.spoilage))

        self.last_supply = dict(supply)
        return wanted, sold, supply, unmet_spend

    def _update_prices(self, wanted, supply):
        """Price chases excess demand, with inertia and hard rails."""
        rate = self.market["price_adjust_rate"]
        smooth = self.market["price_smoothing"]

        for g in self.active_goods():
            # Export demand counts as demand. Without this, merchants could
            # strip a town bare and its prices would never notice.
            w = wanted[g.key] + self.merchant_demand.get(g.key, 0.0)
            s = supply[g.key]
            scale = max(w, s, 1e-6)
            excess = clamp((w - s) / scale, -1.0, 1.0)
            target = g.price * (1.0 + rate * excess)
            g.price = smooth * g.price + (1.0 - smooth) * target
            g.price = clamp(g.price,
                            g.base_price * self.market["price_floor_ratio"],
                            g.base_price * self.market["price_ceiling_ratio"])

    def _score_producers(self, per_producer, sold, supply):
        """Fitness is recent profit, not an abstract 'production score'.

        Sell-through matters: goods you made that nobody bought earn nothing,
        which is the pressure that stops everyone crowding into one crop.
        """
        alpha = self.cfg["simulation"]["fitness_memory"]
        sell_through = {k: (sold[k] / supply[k]) if supply[k] > 1e-9 else 0.0
                        for k in self.goods}
        for p, mine in zip(self.producers, per_producer):
            revenue = sum(qty * self.goods[k].price * sell_through[k]
                          for k, qty in mine.items())
            p.last_revenue = revenue
            p.fitness = (1.0 - alpha) * p.fitness + alpha * revenue

    def _evolve(self):
        """One GA step. Runs *during* the simulation, not before it."""
        sim = self.cfg["simulation"]
        ranked = sorted(self.producers, key=lambda p: p.fitness, reverse=True)
        n_elite = max(1, int(len(ranked) * sim["elite_fraction"]))
        survivors = ranked[:n_elite]

        def tournament():
            picks = self.rng.sample(ranked, min(sim["tournament_size"], len(ranked)))
            return max(picks, key=lambda p: p.fitness)

        children = []
        while len(survivors) + len(children) < len(self.producers):
            a, b = tournament(), tournament()
            mix = self.rng.random()
            logits = {}
            for res in self.unlocked_resources():
                v = mix * a.logits.get(res, 0.0) + (1.0 - mix) * b.logits.get(res, 0.0)
                if self.rng.random() < sim["mutation_rate"]:
                    v += self.rng.gauss(0.0, sim["mutation_scale"])
                logits[res] = v

            # keep logits centred so they cannot drift off to infinity
            mean = sum(logits.values()) / len(logits)
            children.append(Producer(
                logits={k: v - mean for k, v in logits.items()},
                fitness=0.5 * (a.fitness + b.fitness),
            ))

        self.producers = survivors + children

    def step(self, tick):
        while self.scheduled and self.scheduled[0]["tick"] <= tick:
            self.trigger(self.scheduled.pop(0), tick)

        self._advance_discoveries(tick)
        self.modifiers = [m for m in self.modifiers
                          if m.strength_at(tick) is not None or tick < m.start]

        production, per_producer = self._produce(tick)
        self.last_production = dict(production)
        wanted, sold, supply, unmet = self._clear_market(production, tick)
        self._update_prices(wanted, supply)
        self._score_producers(per_producer, sold, supply)

        if tick % self.cfg["simulation"]["evolve_every"] == 0:
            self._evolve()

        agg = {r: 0.0 for r in self.resources}
        for p in self.producers:
            for r, share in p.allocation().items():
                agg[r] += share / len(self.producers)

        for k, g in self.goods.items():
            self.history["price"][k].append(g.price)
            self.history["sold"][k].append(sold.get(k, 0.0))
            self.history["inventory"][k].append(g.inventory)
            self.history["available"][k].append(g.available)
        for r in self.resources:
            self.history["allocation"][r].append(agg[r])
        self.history["unmet"].append(unmet)
        # Goods enjoyed per head, priced at base so inflation cannot fake it.
        self.history["real_consumption"].append(
            sum(sold.get(k, 0.0) * g.base_price for k, g in self.goods.items())
            / max(self.population, 1))
        self.history["treasury"].append(self.treasury)
        self.history["reputation"].append(self.reputation)

    def run(self, ticks=None):
        ticks = ticks or self.cfg["simulation"]["ticks"]
        for t in range(ticks):
            self.step(t)
        return self.history


# A single town is just a world with one market in it.
Economy = Town


# --------------------------------------------------------------------------
# reporting (ASCII, so it works in any terminal with no dependencies)
# --------------------------------------------------------------------------

def _bucket(values, width):
    """Downsample a series to `width` columns by averaging."""
    if not values:
        return [0.0] * width
    out = []
    n = len(values)
    for i in range(width):
        lo = int(i * n / width)
        hi = max(lo + 1, int((i + 1) * n / width))
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out


def _event_axis(marks, total, width):
    axis = [" "] * width
    for tick, ch in marks.items():
        x = min(width - 1, int(tick / max(total, 1) * width))
        axis[x] = ch
    return "".join(axis)


def chart(values, marks, total, width=74, height=9, baseline=None, mark="*"):
    """Plot one series, with an optional dotted reference line at `baseline`.

    One series per chart on purpose: overlaying them means the later series
    overwrites the earlier one wherever they cross, which quietly lies about
    what happened.
    """
    col = _bucket(values, width)
    lo, hi = min(col), max(col)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    grid = [[" "] * width for _ in range(height)]

    if baseline is not None:
        y = int((baseline - lo) / (hi - lo) * (height - 1))
        grid[height - 1 - y] = ["."] * width

    for x, v in enumerate(col):
        y = int((v - lo) / (hi - lo) * (height - 1))
        grid[height - 1 - y][x] = mark

    lines = []
    for i, row in enumerate(grid):
        value = hi - (hi - lo) * i / (height - 1)
        lines.append("  %8.1f |%s" % (value, "".join(row)))
    lines.append("  %8s +%s" % ("", "-" * width))
    lines.append("  %8s  %s" % ("", _event_axis(marks, total, width)))
    return "\n".join(lines)


def stacked_chart(series, order, labels, marks, total, width=74, height=12):
    """Land use as stacked bands. Every column sums to 100%, so nothing hides."""
    cols = {k: _bucket(series[k], width) for k in order}
    grid = [[" "] * width for _ in range(height)]

    for x in range(width):
        column_total = sum(cols[k][x] for k in order) or 1.0
        bounds, cum = [], 0.0
        for k in order:
            cum += cols[k][x] / column_total
            bounds.append((k, cum))
        for row in range(height):
            frac_from_bottom = 1.0 - (row + 0.5) / height
            for k, edge in bounds:
                if frac_from_bottom <= edge:
                    grid[row][x] = labels[k]
                    break

    lines = []
    for i, row in enumerate(grid):
        pct = 100.0 - 100.0 * i / (height - 1)
        lines.append("  %7.0f%% |%s" % (pct, "".join(row)))
    lines.append("  %8s +%s" % ("", "-" * width))
    lines.append("  %8s  %s" % ("", _event_axis(marks, total, width)))
    return "\n".join(lines)


def report(sim, history):
    ticks = len(history["unmet"])
    goods = list(sim.goods.values())
    marks = {t: str(i + 1) for i, (t, _, _) in enumerate(sim.event_log)}
    rule = "=" * 84

    print()
    print(rule)
    print("EVENT TIMELINE")
    print(rule)
    for i, (t, name, flavor) in enumerate(sim.event_log):
        print("  [%s] tick %-4d %-16s %s" % (i + 1, t, name, flavor))

    print()
    print(rule)
    print("PRICES OVER TIME")
    print(rule)
    print("  Each chart is one good. The dotted line is its starting price;")
    print("  the digits on the bottom axis mark the events above.")
    for g in goods:
        print()
        print("  %s   (starts at %.2f)" % (g.name.upper(), g.base_price))
        print(chart(history["price"][g.key], marks, ticks, baseline=g.base_price))

    print()
    print(rule)
    print("LAND USE OVER TIME")
    print(rule)
    rlabels, used = {}, set()
    for r in sim.resources:
        ch = next((c for c in r.upper() if c.isalpha() and c not in used), "?")
        used.add(ch)
        rlabels[r] = ch
    print("  " + "   ".join("%s = %s" % (rlabels[r], sim.resources[r]["name"])
                            for r in sim.resources))
    print(stacked_chart(history["allocation"], list(sim.resources),
                        rlabels, marks, ticks))

    print()
    print(rule)
    print("PRICE RESPONSE TO EACH EVENT")
    print(rule)
    header = "  %-18s" % "event" + "".join("%14s" % g.name for g in goods)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t, name, _ in sim.event_log:
        before = max(0, t - 1)
        after = min(ticks - 1, t + 30)
        row = "  %-18s" % name[:18]
        for g in goods:
            p0 = history["price"][g.key][before]
            p1 = history["price"][g.key][after]
            if p0 < 1e-6 or not history["available"][g.key][before]:
                row += "%14s" % "-"
            else:
                row += "%13.0f%%" % ((p1 / p0 - 1.0) * 100.0)
        print(row)
    print()
    print("  (change from the tick before the event to 30 ticks after)")

    print()
    print(rule)
    print("FINAL STATE")
    print(rule)
    print("  %-14s %9s %9s %11s %9s" % ("good", "price", "vs start", "sold/tick", "stock"))
    for g in goods:
        drift = (g.price / g.base_price - 1.0) * 100.0
        print("  %-14s %9.2f %8.0f%% %11.1f %9.1f" % (
            g.name, g.price, drift, history["sold"][g.key][-1], g.inventory))
    print()
    print("  %-14s %9s" % ("land use", "share"))
    for r in sim.resources:
        print("  %-14s %8.1f%%" % (sim.resources[r]["name"],
                                   history["allocation"][r][-1] * 100.0))
    print()


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# driving the sim from gameplay
# --------------------------------------------------------------------------

def run_with_actions(sim, ticks, actions):
    """Run the sim, injecting player-caused events as they come.

    In a real game you would not precompute `actions` -- you would call
    sim.trigger(event, current_tick) from wherever the player does the thing,
    and sim.step(current_tick) once per in-game day. This function just makes
    that pattern runnable from the command line.
    """
    pending = sorted(actions, key=lambda a: a[0])
    for t in range(ticks):
        while pending and pending[0][0] <= t:
            sim.trigger(pending.pop(0)[1], t)
        sim.step(t)
    return sim.history


# Two things the player might plausibly do, expressed as events. Neither is
# in world.json -- they arrive at runtime through the same trigger() call.
PLAYER_ACTIONS = [
    (95, {
        "type": "disaster", "name": "Granary burned",
        "target": "grain_land", "efficiency_mult": 0.55,
        "duration": 25, "recovery": "linear",
        "flavor": "PLAYER ACTION: you torched the baron's granaries.",
    }),
    (205, {
        "type": "demand_shock", "name": "Trade embargo",
        "target": "mithril_tool", "demand_mult": 0.25, "duration": 35,
        "flavor": "PLAYER ACTION: your embargo cuts off the mithril buyers.",
    }),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--config", default="world.json")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--player-demo", action="store_true",
                    help="also fire two runtime player actions (see PLAYER_ACTIONS)")
    ap.add_argument("--dump", default=None,
                    help="write the full price/allocation history to a JSON file")
    args = ap.parse_args()

    with open(args.config, "r") as f:
        config = json.load(f)

    sim = Economy(config, seed=args.seed)
    ticks = args.ticks or config["simulation"]["ticks"]
    if args.player_demo:
        history = run_with_actions(sim, ticks, PLAYER_ACTIONS)
    else:
        history = sim.run(ticks)
    report(sim, history)

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump({"events": sim.event_log, "history": history}, f, indent=2)
        print("  history written to %s" % args.dump)


if __name__ == "__main__":
    main()
