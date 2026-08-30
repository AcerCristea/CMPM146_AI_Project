"""
Many towns, one economy: merchants as the coupling between local markets.

economy.py simulates a single isolated market. This adds the layer above it.

Each town has its own land quality, so each is naturally good at something
different: Riverfold grows grain, Loomhaven weaves, Ashmoor mines. Left alone
they drift apart -- wheat is cheap where it grows and dear where it does not.
That gap is a profit opportunity, and NPC merchants exist to take it.

A merchant buys where a good is cheap, carries it (taking real ticks to
travel, and paying per leg), and sells where it is dear. Buying pressure
lifts the source price, the delivered cargo depresses the destination price,
and the two converge until the remaining gap is smaller than the cost of
moving goods. Nobody coded "prices should converge" -- it is what happens
when self-interested carriers are allowed to move things.

The interesting consequence is contagion. Blight one town's fields and the
shortage no longer stays local: merchants smell the high price, drain grain
out of the healthy towns to sell into the stricken one, and the whole region
gets more expensive bread. That is the behaviour you cannot get from a
per-town price table.

Stdlib only. Run with:  python trade.py
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from dataclasses import dataclass, field

from economy import Town, chart, stacked_chart, _bucket, clamp


@dataclass
class Merchant:
    """An NPC trader. Greedy, myopic, and that is the point."""
    name: str
    location: str
    gold: float
    min_margin: float
    cargo: dict = field(default_factory=dict)
    destination: str = None
    eta: int = 0
    profit: float = 0.0
    trips: int = 0
    order: dict = None          # what it is bidding for this tick
    idle_ticks: int = 0
    deadheads: int = 0
    home: str = None            # where it started; wealth accrues to here
    by_good: dict = field(default_factory=dict)   # profit per good
    shipment: "Shipment" = None  # the leg currently being carried

    @property
    def in_transit(self):
        return self.destination is not None


@dataclass
class Shipment:
    tick: int
    good: str
    origin: str
    destination: str
    qty: float
    buy_price: float
    sell_price: float
    profit: float


class World:

    def __init__(self, config, seed=None, enable_trade=True):
        self.cfg = copy.deepcopy(config)
        self.rng = random.Random(
            self.cfg["simulation"]["seed"] if seed is None else seed)
        self.enable_trade = enable_trade
        self.trade_cfg = self.cfg.get("trade", {})

        # Towns get the shared goods/resources but no events of their own --
        # the World owns the timeline and dispatches to the right towns.
        town_cfg = copy.deepcopy(self.cfg)
        town_cfg["events"] = []

        self.towns = {}
        for i, (key, spec) in enumerate(self.cfg["towns"].items()):
            self.towns[key] = Town(
                town_cfg,
                seed=self.rng.randint(0, 2 ** 31),
                name=spec["name"],
                population=spec["population"],
                total_land=spec["land"],
                efficiency=spec.get("efficiency"),
            )

        self.routes = {}
        for a, b, legs in self.cfg.get("routes", []):
            self.routes[(a, b)] = legs
            self.routes[(b, a)] = legs

        self.merchants = []
        if enable_trade:
            keys = list(self.towns)
            jitter = self.trade_cfg.get("margin_jitter", 0.0)
            for i in range(self.trade_cfg.get("merchants", 0)):
                self.merchants.append(Merchant(
                    name="merchant-%d" % (i + 1),
                    location=keys[i % len(keys)],
                    home=keys[i % len(keys)],
                    gold=self.trade_cfg.get("starting_gold", 500.0),
                    # Slightly different appetites stop all of them piling
                    # into the same trade on the same tick.
                    min_margin=self.trade_cfg.get("min_margin", 0.1)
                               + self.rng.uniform(0.0, jitter),
                ))

        self.scheduled = sorted(self.cfg.get("events", []), key=lambda e: e["tick"])
        self.event_log = []
        self.shipments = []
        self.exports = {k: 0.0 for k in self.towns}
        self.imports = {k: 0.0 for k in self.towns}
        self.exports_tick = {k: 0.0 for k in self.towns}
        self.export_window = {k: [] for k in self.towns}
        self.history = {
            "spread": {k: [] for k in self.cfg["goods"]},
            "merchant_gold": [],
            "in_transit": [],
            "trade_balance": {k: [] for k in self.towns},
            "gold_by_home": {k: [] for k in self.towns},
        }

    # -- events ------------------------------------------------------------

    def route_legs(self, a, b):
        return self.routes.get((a, b))

    def trigger(self, event, tick):
        """Fire an event. Same game-facing API as the single-town version.

        `town` scopes it to one market; without it the event is regional.
        A discovery is a special case: the deposit lands in one town, but the
        appetite for what it produces (and the collapse in demand for what it
        replaces) is felt everywhere.
        """
        owner = event.get("town")
        for key, town in self.towns.items():
            scoped = dict(event)
            if event["type"] == "discovery":
                if owner and key != owner:
                    scoped.pop("unlocks_resource", None)
            elif owner and key != owner:
                continue
            town.trigger(scoped, tick)

        self.event_log.append((tick, event.get("name", event["type"]),
                               event.get("flavor", ""), owner))

    def _dispatch_scheduled(self, tick):
        while self.scheduled and self.scheduled[0]["tick"] <= tick:
            self.trigger(self.scheduled.pop(0), tick)

    # -- merchant behaviour ------------------------------------------------

    def _arrivals(self, tick):
        """Merchants in transit advance; those that land sell their cargo.

        Selling happens before the town's market opens, so delivered goods
        are genuinely part of today's supply and push the local price down.
        """
        slip = self.trade_cfg.get("slippage", 0.0)
        for m in self.merchants:
            if not m.in_transit:
                continue
            m.eta -= 1
            if m.eta > 0:
                continue

            m.location, m.destination = m.destination, None
            town = self.towns[m.location]
            for good_key, qty in m.cargo.items():
                good = town.goods[good_key]
                revenue = qty * good.price * (1.0 - slip)
                m.gold += revenue
                good.inventory += qty
                if m.shipment is not None:
                    m.shipment.sell_price = good.price
                    m.shipment.profit = revenue - m.shipment.qty * m.shipment.buy_price
                    m.profit += m.shipment.profit
                    m.by_good[good_key] = m.by_good.get(good_key, 0.0) + m.shipment.profit
                    # A town's books: selling abroad earns, buying in costs.
                    self.exports[m.shipment.origin] += (
                        m.shipment.qty * m.shipment.buy_price)
                    self.imports[m.location] += revenue
                    self.exports_tick[m.shipment.origin] += (
                        m.shipment.qty * m.shipment.buy_price)
                    # The town where the sale lands taxes it, and the
                    # merchant sends a cut of the profit back home. This
                    # is what makes a town care which traders are its own.
                    tax = self.trade_cfg.get('tax_rate', 0.0)
                    tribute = self.trade_cfg.get('home_tribute', 0.0)
                    town.treasury += revenue * tax
                    m.gold -= revenue * tax
                    if m.shipment.profit > 0 and m.home:
                        cut = m.shipment.profit * tribute
                        self.towns[m.home].treasury += cut
                        m.gold -= cut
            m.cargo = {}
            m.shipment = None
            m.trips += 1

    def _post_orders(self, tick):
        """Merchants bid BEFORE the local market opens.

        This ordering is the whole trick. If merchants could only pick over
        what households left behind they would find nothing -- a market at
        equilibrium clears to roughly zero stock -- so they would never buy,
        never bid, and never signal that the town should grow an export crop.
        Deadlock: no surplus because no demand, no demand because no surplus.

        Posting the bid first breaks it. An unfilled order still counts as
        demand, so the price rises, so producers plant more, so next time
        there is something to actually buy. That is how an export industry
        bootstraps itself here.
        """
        for town in self.towns.values():
            town.merchant_demand = {k: 0.0 for k in town.goods}
        for m in self.merchants:
            m.order = None

        if not self.enable_trade:
            return

        slip = self.trade_cfg.get("slippage", 0.0)
        per_leg = self.trade_cfg.get("transport_cost_per_leg", 0.0)
        capacity = self.trade_cfg.get("cargo_capacity", 100.0)

        # A market can only absorb so much export interest. Without this cap
        # the fleet can bid for ten times what a town produces, that bid
        # swamps the local price signal, and every town ends up farming the
        # single most-traded good regardless of what its land is good for.
        bid_cap = self.trade_cfg.get("export_bid_cap", 0.6)
        allowance = {}
        for key, town in self.towns.items():
            for good_key, good in town.goods.items():
                allowance[(key, good_key)] = max(
                    good.inventory, town.last_supply.get(good_key, 0.0)) * bid_cap

        queue = list(self.merchants)
        self.rng.shuffle(queue)

        for m in queue:
            if m.in_transit:
                continue
            here = self.towns[m.location]
            best = None

            for good_key, good in here.goods.items():
                if not good.available:
                    continue
                # Buy at the source only. Allowing merchants to re-export
                # goods another caravan just dropped off lets them ping-pong
                # a cargo between two towns that produce none of it, each
                # trade moving the thin local price enough to fund the next.
                # That is not arbitrage, it is a pump.
                if here.last_production.get(good_key, 0.0) < 1.0:
                    continue
                buy_price = good.price * (1.0 + slip)
                if buy_price <= 1e-6:
                    continue
                room = allowance.get((m.location, good_key), 0.0)
                if room < 1.0:
                    continue

                for dest_key, dest in self.towns.items():
                    if dest_key == m.location:
                        continue
                    legs = self.route_legs(m.location, dest_key)
                    if legs is None or not dest.goods[good_key].available:
                        continue

                    sell_price = dest.goods[good_key].price * (1.0 - slip)
                    unit_profit = sell_price - buy_price - per_leg * legs
                    if unit_profit / buy_price < m.min_margin:
                        continue

                    qty = min(capacity, m.gold / buy_price, room)
                    if qty < 1.0:
                        continue

                    score = unit_profit * qty
                    if best is None or score > best[0]:
                        best = (score, good_key, dest_key, qty, legs)

            if best is None:
                continue

            _, good_key, dest_key, qty, legs = best
            m.order = {"good": good_key, "dest": dest_key,
                       "qty": qty, "legs": legs}
            here.merchant_demand[good_key] += qty
            allowance[(m.location, good_key)] -= qty

    def _fill_orders(self, tick):
        """Fill what the market can actually supply, then set out."""
        slip = self.trade_cfg.get("slippage", 0.0)
        max_frac = self.trade_cfg.get("max_buy_fraction", 0.35)

        for m in self.merchants:
            if m.in_transit or not m.order:
                continue
            here = self.towns[m.location]
            good = here.goods[m.order["good"]]

            buy_price = good.price * (1.0 + slip)
            qty = min(m.order["qty"], good.inventory * max_frac,
                      m.gold / max(buy_price, 1e-6))
            if qty < 1.0:
                continue    # nothing to fill; the bid stood, prices heard it

            good.inventory -= qty
            m.gold -= qty * buy_price
            m.cargo = {m.order["good"]: qty}
            m.destination = m.order["dest"]
            m.eta = m.order["legs"]
            m.idle_ticks = 0
            m.shipment = Shipment(
                tick=tick, good=m.order["good"], origin=m.location,
                destination=m.order["dest"], qty=qty, buy_price=buy_price,
                sell_price=0.0, profit=0.0)
            self.shipments.append(m.shipment)
            m.order = None

        self._deadhead(tick)

    def _deadhead(self, tick):
        """Move stranded merchants, empty, to where the goods actually are.

        Without this they pile up in whichever town has nothing to sell,
        bidding for stock that is not there, and the caravan fleet gradually
        parks itself in the poorest market.
        """
        patience = self.trade_cfg.get("patience", 4)
        for m in self.merchants:
            if m.in_transit or m.cargo:
                continue
            if m.order is None and not self.enable_trade:
                continue

            m.idle_ticks += 1
            if m.idle_ticks < patience:
                continue

            best = None
            for key, town in self.towns.items():
                if key == m.location or self.route_legs(m.location, key) is None:
                    continue
                stock = sum(g.inventory * g.price for g in town.goods.values()
                            if g.available)
                legs = self.route_legs(m.location, key)
                score = stock / max(legs, 1)
                if best is None or score > best[0]:
                    best = (score, key, legs)

            if best and best[0] > 0:
                _, key, legs = best
                m.destination = key
                m.eta = legs
                m.order = None
                m.idle_ticks = 0
                m.deadheads += 1

    # -- the loop ----------------------------------------------------------

    def _update_reputation(self, tick):
        """Standing among the towns, as a relative index around 50.

        Three things a medieval town would actually be judged on, and none of
        them is military: how much of the region's trade flows out of your
        gates, whether your own people are eating, and what is in your coffers.
        It is deliberately relative -- the three scores are measured against
        each other, so one town rises only when another slips. That is the
        competition, without anyone raising an army.
        """
        window = self.trade_cfg.get("reputation_window", 30)
        for k in self.towns:
            w = self.export_window[k]
            w.append(self.exports_tick[k])
            if len(w) > window:
                w.pop(0)
        self.exports_tick = {k: 0.0 for k in self.towns}

        n = len(self.towns)
        trade = {k: sum(self.export_window[k]) for k in self.towns}
        total_trade = sum(trade.values())

        food = {k: (t.history["real_consumption"][-1]
                    if t.history["real_consumption"] else 0.0)
                for k, t in self.towns.items()}
        total_food = sum(food.values())

        coin = {k: t.treasury / max(t.population, 1) for k, t in self.towns.items()}
        total_coin = sum(coin.values())

        def share(d, total):
            # 1.0 means "an even slice"; above means outperforming the others
            return (d / total * n) if total > 1e-9 else 1.0

        alpha = self.trade_cfg.get("reputation_smoothing", 0.05)
        for k, town in self.towns.items():
            raw = (0.40 * share(trade[k], total_trade)
                   + 0.30 * share(food[k], total_food)
                   + 0.30 * share(coin[k], total_coin))
            target = clamp(50.0 * raw, 0.0, 100.0)
            town.reputation = (1 - alpha) * town.reputation + alpha * target

    def step(self, tick):
        self._dispatch_scheduled(tick)
        self._arrivals(tick)      # cargo lands and becomes local supply
        self._post_orders(tick)   # merchants bid, moving the price
        for town in self.towns.values():
            town.step(tick)       # production, households, new prices
        self._fill_orders(tick)   # buy what the market could supply
        self._update_reputation(tick)

        for good_key in self.cfg["goods"]:
            prices = [t.goods[good_key].price for t in self.towns.values()
                      if t.goods[good_key].available]
            if len(prices) < 2:
                self.history["spread"][good_key].append(0.0)
            else:
                mean = sum(prices) / len(prices)
                self.history["spread"][good_key].append(
                    (max(prices) - min(prices)) / mean * 100.0)
        self.history["merchant_gold"].append(sum(m.gold for m in self.merchants))
        self.history["in_transit"].append(
            sum(1 for m in self.merchants if m.in_transit))
        for k in self.towns:
            self.history["trade_balance"][k].append(
                self.exports[k] - self.imports[k])
            self.history["gold_by_home"][k].append(
                sum(m.gold for m in self.merchants if m.home == k))

    def run(self, ticks=None):
        ticks = ticks or self.cfg["simulation"]["ticks"]
        for t in range(ticks):
            self.step(t)
        return self.history


# --------------------------------------------------------------------------
# terminal report
# --------------------------------------------------------------------------

def report(world, ticks):
    rule = "=" * 84
    marks = {t: str(i + 1) for i, (t, _, _, _) in enumerate(world.event_log)}
    goods = [g for g in list(world.towns.values())[0].goods.values()]

    print()
    print(rule)
    print("THE REGION")
    print(rule)
    print("  %-12s %6s %6s   %s" % ("town", "pop", "land", "land quality"))
    for key, town in world.towns.items():
        qual = "  ".join("%s x%.2f" % (r.split("_")[0], v)
                         for r, v in town.efficiency_bonus.items())
        print("  %-12s %6d %6.0f   %s" % (town.name, town.population,
                                          town.total_land, qual))
    print()
    print("  routes:  " + "   ".join(
        "%s-%s %d ticks" % (a[:4], b[:4], n)
        for (a, b), n in sorted(world.routes.items()) if a < b))
    print("  %d merchants, %.0f gold each, %.0f cargo capacity" % (
        len(world.merchants),
        world.trade_cfg.get("starting_gold", 0),
        world.trade_cfg.get("cargo_capacity", 0)))

    print()
    print(rule)
    print("EVENT TIMELINE")
    print(rule)
    for i, (t, name, flavor, owner) in enumerate(world.event_log):
        where = world.towns[owner].name if owner else "region-wide"
        print("  [%s] tick %-4d %-16s (%s)" % (i + 1, t, name, where))
        print("      %s" % flavor)

    print()
    print(rule)
    print("PRICE GAP BETWEEN TOWNS  (max-min as %% of mean; 0 = one single market)")
    print(rule)
    for g in goods:
        series = world.history["spread"][g.key]
        if max(series) < 1e-9:
            continue
        print()
        print("  %s" % g.name.upper())
        print(chart(series, marks, ticks, height=7))

    print()
    print(rule)
    print("TRADE FLOWS")
    print(rule)
    done = [s for s in world.shipments if s.sell_price > 0]
    print("  %d shipments delivered, %d still on the road" % (
        len(done), len(world.shipments) - len(done)))
    if done:
        lanes = {}
        for s in done:
            key = (s.good, s.origin, s.destination)
            agg = lanes.setdefault(key, [0.0, 0.0, 0])
            agg[0] += s.qty
            agg[1] += s.profit
            agg[2] += 1
        print()
        print("  %-14s %-24s %8s %10s %8s" % (
            "good", "route", "trips", "units", "profit"))
        for (good, a, b), (qty, profit, n) in sorted(
                lanes.items(), key=lambda kv: -kv[1][0]):
            route = "%s -> %s" % (world.towns[a].name, world.towns[b].name)
            print("  %-14s %-24s %8d %10.0f %8.0f" % (
                world.towns[a].goods[good].name, route, n, qty, profit))

    print()
    print("  %-14s %8s %8s %10s" % ("merchant", "trips", "gold", "profit"))
    for m in sorted(world.merchants, key=lambda m: -m.profit):
        print("  %-14s %8d %8.0f %10.0f" % (m.name, m.trips, m.gold, m.profit))

    print()
    print(rule)
    print("WEALTH AND POWER")
    print(rule)
    print("  Real consumption per head -- goods people actually got, priced at")
    print("  base so a price spike cannot masquerade as prosperity.")
    print()
    print("  %-12s %12s %12s %10s" % ("town", "first third", "last third", "change"))
    print("  " + "-" * 50)
    for town in world.towns.values():
        rc = town.history["real_consumption"]
        third = max(1, len(rc) // 3)
        early = sum(rc[:third]) / third
        late = sum(rc[-third:]) / third
        print("  %-12s %12.1f %12.1f %9.0f%%" % (
            town.name, early, late, (late / early - 1) * 100 if early else 0))

    if world.merchants:
        print()
        print("  Merchants by where they started. They roam freely, so this is")
        print("  origin, not allegiance -- see how little it separates them.")
        print()
        print("  %-12s %10s %8s %12s %12s" % (
            "home town", "merchants", "trips", "gold", "profit"))
        print("  " + "-" * 58)
        for key, town in world.towns.items():
            crew = [m for m in world.merchants if m.home == key]
            if not crew:
                continue
            print("  %-12s %10d %8d %12.0f %12.0f" % (
                town.name, len(crew), sum(m.trips for m in crew),
                sum(m.gold for m in crew), sum(m.profit for m in crew)))

        print()
        print("  %-12s %-11s %6s %11s %11s   %s" % (
            "merchant", "home", "trips", "gold", "profit", "earned it on"))
        print("  " + "-" * 82)
        for m in sorted(world.merchants, key=lambda m: -m.profit)[:6]:
            top = sorted(m.by_good.items(), key=lambda kv: -kv[1])[:2]
            mix = ", ".join("%s %.0f%%" % (world.towns[m.home].goods[g].name, p / max(m.profit, 1) * 100)
                            for g, p in top)
            print("  %-12s %-11s %6d %11.0f %11.0f   %s" % (
                m.name, world.towns[m.home].name, m.trips, m.gold, m.profit, mix))

        print()
        print("  %-12s %11s %12s %12s %10s" % (
            "town", "reputation", "treasury", "exports", "prosperity"))
        print("  " + "-" * 62)
        for town in sorted(world.towns.values(), key=lambda t: -t.reputation):
            key = [k for k, v in world.towns.items() if v is town][0]
            print("  %-12s %11.1f %12.0f %12.0f %10.1f" % (
                town.name, town.reputation, town.treasury, world.exports[key],
                town.history["real_consumption"][-1]))
        print("  (reputation is relative: 50 is par, and one town rises only")
        print("   when another slips. 40% trade share, 30% how well fed, 30% coin.)")

        gross_x = sum(world.exports.values())
        gross_i = sum(world.imports.values())
        take = sum(m.profit for m in world.merchants)
        print()
        print("  Regional books:")
        print("    towns earned selling exports   %10.0f" % gross_x)
        print("    towns paid buying imports      %10.0f" % gross_i)
        print("    the middlemen kept             %10.0f  (%.0f%% of turnover)" % (
            take, take / max(gross_i, 1) * 100))

    print()
    print(rule)
    print("FINAL PRICES BY TOWN")
    print(rule)
    header = "  %-14s" % "good" + "".join("%12s" % t.name for t in world.towns.values())
    print(header + "%10s" % "gap")
    print("  " + "-" * (len(header) + 8))
    for g in goods:
        prices = [t.goods[g.key].price for t in world.towns.values()]
        row = "  %-14s" % g.name + "".join("%12.2f" % p for p in prices)
        mean = sum(prices) / len(prices)
        row += "%9.0f%%" % ((max(prices) - min(prices)) / mean * 100.0)
        print(row)

    print()
    print(rule)
    print("WHAT EACH TOWN ENDED UP DOING WITH ITS LAND")
    print(rule)
    resources = list(list(world.towns.values())[0].resources)
    header = "  %-14s" % "town" + "".join("%14s" % r.split("_")[0] for r in resources)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for town in world.towns.values():
        row = "  %-14s" % town.name
        for r in resources:
            row += "%13.0f%%" % (town.history["allocation"][r][-1] * 100.0)
        print(row)
    print()


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Multi-town economy with trade.")
    ap.add_argument("--config", default="towns.json")
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-trade", action="store_true",
                    help="run the same world with the merchants removed")
    ap.add_argument("--html", default=None,
                    help="write an interactive HTML report to this path")
    args = ap.parse_args()

    with open(args.config, "r") as f:
        config = json.load(f)

    ticks = args.ticks or config["simulation"]["ticks"]
    world = World(config, seed=args.seed, enable_trade=not args.no_trade)
    world.run(ticks)
    report(world, ticks)

    if args.html:
        import report_html
        # A second run with the merchants deleted, so the page can show what
        # trade is actually responsible for rather than just asserting it.
        baseline = World(config, seed=args.seed, enable_trade=False)
        baseline.run(ticks)
        report_html.write(world, ticks, args.html, baseline=baseline)
        print("  wrote %s" % args.html)


if __name__ == "__main__":
    main()
