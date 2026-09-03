"""
The single-city game layer: one market, and a player who can move it.

economy.py simulates a market. trade.py wires several of them together with
caravans. This is the third thing, and the one that turns either of them into
a game feature: a player who trades in the market, hears about what is
happening through rumour rather than through a debug readout, takes on
quests that change what the country can physically produce, and sells across
a border to a foreign market that anchors prices from outside.

Four pieces, in the order they matter:

  Player         buys and sells, and those orders reach the price. Capped so
                 one person moves a market without breaking a country.
  RumourMill     the legibility layer. A price that moves for reasons the
                 player cannot discover reads as the game cheating. Rumours
                 are how a cause becomes findable in-world -- late, sometimes
                 exaggerated, occasionally wrong.
  QUESTS         world events with quest names. Clearing bandits out of a
                 mine is an efficiency modifier; it is also a thing to do on
                 a Tuesday, and the tool price falling a week later is the
                 payoff.
  ForeignMarket  an abstracted outside world, so a single city is not a
                 sealed box. It buys exports and sells imports at drifting
                 prices, which anchors local prices and gives the player
                 somewhere to arbitrage.

Stdlib only. Run with:  python kingdom.py
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field

from economy import Town, clamp


# --------------------------------------------------------------------------
# the player
# --------------------------------------------------------------------------

@dataclass
class Trade:
    tick: int
    action: str          # "buy" | "sell"
    good: str
    qty: float
    unit_price: float
    total: float


@dataclass
class Player:
    gold: float
    inventory: dict = field(default_factory=dict)
    ledger: list = field(default_factory=list)

    def holding(self, good_key):
        return self.inventory.get(good_key, 0.0)

    def net_worth(self, town):
        """Gold plus stock valued at what the market would pay today."""
        goods = sum(qty * town.goods[k].price for k, qty in self.inventory.items())
        return self.gold + goods


# --------------------------------------------------------------------------
# rumours
# --------------------------------------------------------------------------

@dataclass
class Rumour:
    tick: int            # the day it becomes sayable
    text: str
    kind: str            # "event" | "price" | "shortage" | "quest"
    reliable: bool = True


class RumourMill:
    """Turns things that happened into things people say.

    Deliberately imperfect. Word takes days to arrive, magnitudes get
    exaggerated in the retelling, and some of it is simply wrong -- which is
    the point, because a player who can tell a good rumour from a bad one has
    something to be skilled at.
    """

    OPENERS = [
        "Word from the road:", "They are saying in the tavern:",
        "A carter come in this morning:", "The crier had it at dawn:",
        "Overheard at the weighbridge:",
    ]

    def __init__(self, rng, cfg):
        self.rng = rng
        self.delay = cfg.get("rumour_delay", 3)
        self.delay_jitter = cfg.get("rumour_delay_jitter", 3)
        self.price_window = cfg.get("rumour_price_window", 7)
        self.price_threshold = cfg.get("rumour_price_threshold", 0.15)
        self.false_rate = cfg.get("rumour_false_rate", 0.12)
        self.pending = []
        self.heard = []
        self._last_spoken = {}

    def _say(self, tick, text, kind, reliable=True):
        when = tick + self.delay + self.rng.randint(0, self.delay_jitter)
        self.pending.append(Rumour(when, text, kind, reliable))

    def _retell_rise(self, pct):
        """How a 22% rise gets retold. Full predicates, so the line reads."""
        if pct > 55:
            return self.rng.choice([
                "has tripled", "has gone through the roof", "has more than doubled"])
        if pct > 30:
            return self.rng.choice([
                "has doubled", "is half again as dear", "is climbing fast"])
        return self.rng.choice([
            "is dearer by the week", "keeps creeping up", "is on the rise"])

    def _retell_fall(self, pct):
        if pct < -40:
            return self.rng.choice([
                "is not worth carting", "has collapsed", "cannot be given away"])
        return self.rng.choice([
            "is going cheap", "has come right down", "is soft at the moment"])

    def on_event(self, tick, name, flavor):
        self._say(tick, "%s %s" % (self.rng.choice(self.OPENERS), flavor or name),
                  "event")

    def on_quest(self, tick, text):
        # the player did this themselves, so no delay and no distortion
        self.pending.append(Rumour(tick, text, "quest", True))

    def observe(self, tick, town):
        """Look at the market and gossip about anything worth gossiping about."""
        for good in town.active_goods():
            hist = town.history["price"][good.key]
            if len(hist) <= self.price_window:
                continue
            now, before = hist[-1], hist[-1 - self.price_window]
            if before < 1e-6:
                continue
            change = (now / before - 1.0) * 100.0

            if abs(change) >= self.price_threshold * 100 and \
                    tick - self._last_spoken.get(("price", good.key), -99) >= 14:
                self._last_spoken[("price", good.key)] = tick
                # some of what gets said is simply not so, and a player who
                # cannot tell which is which has something to get good at
                if self.rng.random() < self.false_rate:
                    wrong = self._retell_fall(-50) if change > 0 else self._retell_rise(50)
                    line = "%s %s %s, or so they reckon." % (
                        self.rng.choice(self.OPENERS), good.name, wrong)
                    self._say(tick, line, "price", reliable=False)
                else:
                    told = self._retell_rise(change) if change > 0 \
                        else self._retell_fall(change)
                    line = "%s %s %s." % (
                        self.rng.choice(self.OPENERS), good.name, told)
                    self._say(tick, line, "price", reliable=True)

            # An empty shelf at closing time is a market clearing normally.
            # A shortage is demand that went home unfilled.
            unmet = town.history["unmet_units"][good.key]
            sold = town.history["sold"][good.key][-1]
            if unmet and unmet[-1] > 0.25 * max(sold + unmet[-1], 1.0) and \
                    tick - self._last_spoken.get(("short", good.key), -99) >= 20:
                self._last_spoken[("short", good.key)] = tick
                self._say(tick, "%s folk are being turned away from the %s stalls." % (
                    self.rng.choice(self.OPENERS), good.name.lower()), "shortage")

    def today(self, tick):
        """Rumours that have finished travelling and are being said now."""
        ready = [r for r in self.pending if r.tick <= tick]
        self.pending = [r for r in self.pending if r.tick > tick]
        self.heard.extend(ready)
        return ready


# --------------------------------------------------------------------------
# quests: world events with names a player would recognise
# --------------------------------------------------------------------------

QUESTS = {
    "clear_the_mine": {
        "cost": 450,
        "name": "Clear the bandits from the iron mine",
        "said": "The mine road is safe again. The smiths are hiring.",
        "event": {"type": "disaster", "name": "Mine reopened",
                  "target": "ore_land", "efficiency_mult": 1.75,
                  "duration": 45, "recovery": "linear",
                  "flavor": "The bandits are cleared and the mine is working again."},
    },
    "escort_the_grain_convoy": {
        "cost": 300,
        "name": "Escort the grain convoy in from the coast",
        "said": "The convoy came through whole. The granaries are full for once.",
        "event": {"type": "stock", "name": "Grain convoy",
                  "changes": {"wheat": 900.0},
                  "flavor": "A relief convoy unloads at the granary."},
    },
    # Note this one is an efficiency hit, not a stock deletion. A market that
    # clears every day holds about half a day of cloth on the shelf, so
    # "destroy 400 cloth" destroys the 36 that happen to be there and moves
    # the price by nothing. What actually hurts is the weavers losing their
    # looms for a fortnight. Adding stock works fine -- see the grain convoy --
    # because you can put down more than is already there. Taking it away
    # cannot exceed what exists.
    "burn_the_rival_warehouse": {
        "cost": 260,
        "name": "Burn out the rival cloth halls",
        "said": "The cloth halls went up in the night. Nobody saw a thing.",
        "event": {"type": "disaster", "name": "Cloth halls burned",
                  "target": "cotton_land", "efficiency_mult": 0.35,
                  "duration": 22, "recovery": "linear",
                  "flavor": "The cloth halls burn. The weavers have lost their looms."},
    },
    "bring_back_the_ore_sample": {
        "cost": 150,
        "name": "Bring the strange ore back to the assayer",
        "said": "The assayer says the grey ore takes an edge no iron ever held.",
        "event": {"type": "discovery", "name": "Mithril assayed",
                  "unlocks_resource": "mithril_vein", "introduces": "mithril_tool",
                  "substitutes_for": "tool", "demand_capture": 0.6,
                  "adoption_ticks": 25, "new_need": 0.10,
                  "flavor": "The assayer confirms it: mithril, and it is better."},
    },
    "spread_word_of_famine": {
        "cost": 220,
        "name": "Spread word that the harvest has failed",
        "said": "Everyone is buying grain. Nobody can say quite who started it.",
        "event": {"type": "demand_shock", "name": "Hoarding",
                  "targets": {"wheat": 1.9}, "duration": 18,
                  "flavor": "Panic buying. Every household wants a full grain bin."},
    },
}


# What an adventurer can carry. Better metal means the risky jobs go wrong
# less often -- which, in this sim, means fewer burned fields.
GEAR = {
    "iron":    {"name": "iron kit",    "bonus": 0.00, "metal": None,           "units": 0},
    "mithril": {"name": "mithril kit", "bonus": 0.15, "metal": "mithril_tool", "units": 12},
}


# --------------------------------------------------------------------------
# the world outside the border
# --------------------------------------------------------------------------

class ForeignMarket:
    """One abstracted trading partner beyond the border.

    Not a simulated town -- just a set of prices that drift on their own and
    a willingness to trade at them. It does two useful jobs: it stops a single
    city being a sealed box where prices can wander anywhere, and it gives the
    player a second price to compare against, which is the whole basis of
    smuggling.
    """

    def __init__(self, town, cfg, rng):
        self.rng = rng
        self.enabled = cfg.get("enabled", True)
        self.band = cfg.get("freight_band", 0.14)
        self.max_fraction = cfg.get("max_fraction", 0.22)
        self.drift = cfg.get("drift", 0.012)
        self.pull = cfg.get("mean_reversion", 0.02)
        self.prices = {k: g.base_price for k, g in town.goods.items()}
        self.flows = {k: 0.0 for k in town.goods}   # + imported, - exported

    def step(self, town, tick):
        if not self.enabled:
            return []
        moves = []
        for key, good in town.goods.items():
            # foreign prices wander, but not far and not forever
            p = self.prices[key]
            p += self.rng.gauss(0.0, self.drift) * good.base_price
            p += (good.base_price - p) * self.pull
            self.prices[key] = max(good.base_price * 0.35, p)

            if not good.available:
                continue

            cap = max(town.last_supply.get(key, 0.0), 1.0) * self.max_fraction
            local, foreign = good.price, self.prices[key]

            if local < foreign * (1.0 - self.band):
                # cheap here: they buy it off us, which lifts our price
                gap = (foreign * (1.0 - self.band) - local) / max(local, 1e-6)
                qty = min(cap, cap * clamp(gap * 3.0, 0.0, 1.0), good.inventory)
                if qty >= 1.0:
                    good.inventory -= qty
                    town.extra_demand[key] += qty
                    self.flows[key] -= qty
                    moves.append(("export", key, qty, foreign))

            elif local > foreign * (1.0 + self.band):
                # dear here: they ship it in, which pushes our price back down
                gap = (local - foreign * (1.0 + self.band)) / max(local, 1e-6)
                qty = min(cap, cap * clamp(gap * 3.0, 0.0, 1.0))
                if qty >= 1.0:
                    good.inventory += qty
                    self.flows[key] += qty
                    moves.append(("import", key, qty, foreign))
        return moves


# --------------------------------------------------------------------------
# the game-facing object
# --------------------------------------------------------------------------

class Kingdom:
    """One country, one market, one player. The API a game would call.

        k = Kingdom(config)
        k.step(day)                     once per in-game day
        k.price_board()                 what the player sees on the board
        k.buy("wheat", 180, day)        and the price moves
        k.do_quest("clear_the_mine", day)
        k.whats_being_said(day)         rumours, not debug output
    """

    def __init__(self, config, seed=None):
        self.cfg = config
        sim = config["simulation"]
        self.rng = random.Random(sim["seed"] if seed is None else seed)
        self.town = Town(config, seed=sim["seed"] if seed is None else seed,
                         name=config.get("country", {}).get("name", "the capital"))

        pcfg = config.get("player", {})
        self.player = Player(gold=pcfg.get("starting_gold", 2000.0))
        self.max_order = pcfg.get("max_order_fraction", 0.28)
        self.spread = pcfg.get("spread", 0.03)
        self.stall_fraction = pcfg.get("stall_fraction", 0.18)

        seed_base = sim["seed"] if seed is None else seed
        warm = sim.get("warmup_days", 40)
        for t in range(-warm, 0):
            self.town.step(t)

        self.foreign = ForeignMarket(self.town, config.get("foreign_market", {}),
                                     random.Random(seed_base + 1))
        self.rumours = RumourMill(self.rng, config.get("rumours", {}))
        self.quest_log = []
        self.done_quests = set()
        self.jobs_taken = []
        # Every economic mark the player has left. Kept so the whole run
        # can be replayed without them, which is the only honest way to
        # answer 'how much of this was me?'
        self.footprints = []
        # The loop that makes playing well pay: wages track how well the
        # town is doing, and gear unlocks when the town finds the metal.
        wcfg = config.get("wages", {})
        self.wage_norm = wcfg.get("norm", 15.0)
        self.wage_floor = wcfg.get("floor", 0.75)
        self.wage_ceiling = wcfg.get("ceiling", 2.0)
        self.wage_sensitivity = wcfg.get("sensitivity", 2.5)
        self.gear = "iron"
        self.earned = 0.0
        self.wage_log = []
        self.last_pay = 0.0
        # You are a person in this town, not a spreadsheet above it.
        self.standing = 0            # what the town thinks of you
        self.food_spend = 0.0        # what living here has cost you
        self.hungry_weeks = 0
        self.rations = pcfg.get("rations_per_day", 2.0)
        self.day = 0

    # -- what the player can see -------------------------------------------

    def price_board(self):
        return {g.key: {"name": g.name, "price": g.price,
                        "for_sale": g.inventory,
                        "you_hold": self.player.holding(g.key)}
                for g in self.town.active_goods()}

    def whats_being_said(self, tick):
        return self.rumours.today(tick)

    # -- trading -----------------------------------------------------------

    def _order_cap(self, good_key):
        """One player should move a market, not empty a country."""
        daily = max(self.town.last_supply.get(good_key, 0.0), 1.0)
        return daily * self.max_order

    def stall_stock(self, good_key):
        """What the stallholders have kept back for travellers.

        A market at equilibrium clears to almost nothing by evening, so if the
        player could only buy the leftovers there would never be anything to
        buy. Stallholders hold a slice of the day's supply back for whoever
        walks up with coin -- which is both how a market actually works and
        how a shop in a game has to work.
        """
        daily = self.town.last_supply.get(good_key, 0.0)
        return max(self.town.goods[good_key].inventory, daily * self.stall_fraction)

    def buy(self, good_key, qty, tick):
        good = self.town.goods[good_key]
        if not good.available:
            return {"filled": 0.0, "reason": "no such good on sale"}

        allowed = min(qty, self._order_cap(good_key), self.stall_stock(good_key))
        if allowed < 1.0:
            return {"filled": 0.0, "reason": "nothing to be had"}

        unit = good.price * (1.0 + self.spread)
        affordable = self.player.gold / unit
        filled = min(allowed, affordable)
        if filled < 1.0:
            return {"filled": 0.0, "reason": "cannot afford it"}

        cost = filled * unit
        self.player.gold -= cost
        self.player.inventory[good_key] = self.player.holding(good_key) + filled
        good.inventory = max(0.0, good.inventory - filled)
        # the order is demand, and demand is what moves the price
        self.town.extra_demand[good_key] += filled
        self.player.ledger.append(Trade(tick, "buy", good_key, filled, unit, -cost))
        return {"filled": filled, "unit_price": unit, "cost": cost,
                "capped": filled < qty - 1e-6}

    def sell(self, good_key, qty, tick):
        held = self.player.holding(good_key)
        filled = min(qty, held)
        if filled < 1.0:
            return {"filled": 0.0, "reason": "you have none to sell"}

        good = self.town.goods[good_key]
        unit = good.price * (1.0 - self.spread)
        revenue = filled * unit
        self.player.gold += revenue
        self.player.inventory[good_key] = held - filled
        good.inventory += filled           # extra supply pushes the price down
        self.player.ledger.append(Trade(tick, "sell", good_key, filled, unit, revenue))
        return {"filled": filled, "unit_price": unit, "revenue": revenue}

    # -- what the town gives back ------------------------------------------

    def prosperity(self, days=30):
        hist = self.town.history["real_consumption"]
        if not hist:
            return self.wage_norm
        window = hist[-days:]
        return sum(window) / len(window)

    def wage_multiplier(self):
        """How much work pays here, against an ordinary town.

        A town whose people are eating fifteen percent better has fifteen
        percent more to spend, and the guilds pay accordingly. This is the
        one loop that runs the right way in the sim: shore a shaft, and the
        smiths are hiring at better rates for years. Cheaper gear does not
        work as a reward -- prices spring back -- but wages do not, because
        they follow prosperity, which is the thing that actually accumulates.
        """
        ratio = self.prosperity() / max(self.wage_norm, 1e-6)
        mult = 1.0 + self.wage_sensitivity * (ratio - 1.0)
        return clamp(mult, self.wage_floor, self.wage_ceiling)

    def gear_bonus(self):
        return GEAR[self.gear]["bonus"]

    def gear_price(self, key):
        spec = GEAR[key]
        good = self.town.goods.get(spec["metal"]) if spec["metal"] else None
        if good is None or not good.available:
            return None
        return good.price * spec["units"]

    def buy_gear(self, key, tick):
        price = self.gear_price(key)
        if price is None:
            return {"ok": False, "reason": "nobody here works that metal yet"}
        if price > self.player.gold:
            return {"ok": False, "reason": "it costs %.0fg and you have %.0fg" % (
                price, self.player.gold)}
        self.player.gold -= price
        self.gear = key
        # A sword is a dozen tools' worth of metal, and that is demand.
        self.town.extra_demand[GEAR[key]["metal"]] += GEAR[key]["units"]
        self.player.ledger.append(Trade(tick, "buy", GEAR[key]["metal"],
                                        GEAR[key]["units"], price / GEAR[key]["units"],
                                        -price))
        return {"ok": True, "price": price}

    # -- doing things to the world -----------------------------------------

    def available_quests(self):
        """Jobs not yet taken. Each one can only be done once."""
        return {k: q for k, q in QUESTS.items() if k not in self.done_quests}

    def do_quest(self, key, tick):
        # Repeats are allowed: what stops you doing the same thing every week
        # is the decision deck's cooldown, not a flag here. A game calling
        # this directly may well want to clear the mine road twice.
        quest = QUESTS[key]
        cost = quest.get("cost", 0)
        if self.player.gold < cost:
            return {"ok": False,
                    "reason": "that costs %dg and you have %dg" % (cost, self.player.gold)}
        self.player.gold -= cost
        event = dict(quest["event"])
        if event["type"] == "stock":
            for good_key, delta in event["changes"].items():
                good = self.town.goods[good_key]
                good.inventory = max(0.0, good.inventory + delta)
            self.town.event_log.append((tick, event["name"], event.get("flavor", "")))
        else:
            self.town.trigger(event, tick)
        self.rumours.on_quest(tick, quest["said"])
        self.quest_log.append((tick, quest["name"]))
        self.done_quests.add(key)
        return {"ok": True, "name": quest["name"], "cost": cost}

    # -- the day -----------------------------------------------------------

    def step(self, tick):
        self.day = tick
        before = len(self.town.event_log)
        self.town.step(tick)
        # anything the world did today becomes something people talk about
        for t, name, flavor in self.town.event_log[before:]:
            self.rumours.on_event(t, name, flavor)
        self.foreign.step(self.town, tick)
        self.rumours.observe(tick, self.town)


# --------------------------------------------------------------------------
# a scripted play session, so the whole thing can be watched happening
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# outings: ordinary adventuring, with a footprint
# --------------------------------------------------------------------------
#
# This is the part that is actually worth pitching to a game.
#
# You are not making economic decisions. You are killing goblins in a mine
# shaft because somebody asked you to, and testing a fire ward for a hedge
# mage because she is paying. Nobody sits down to move the grain market. But
# the goblins were sitting on the ore road, and the fire ward went wrong and
# took four acres of wheat with it, and both of those are physical facts the
# market has to deal with.
#
# So every outing carries a FOOTPRINT: a small, usually invisible change to
# what the county can produce. Three percent on the ore yield for a month.
# Six percent off the grain fields for a season. On its own, nothing -- less
# than the noise the market makes on a quiet week. Forty of them over a year
# is a different county.
#
# The point of the demo is the last screen, not the first. At the end the
# whole run is replayed with your footprints removed, and the difference
# between the two is the answer to a question no game currently asks: what
# did all that adventuring actually do to the place?


@dataclass
class Outcome:
    """What happened, and the mark it left."""
    text: str
    pay: float = 0.0
    event: dict = None       # the small physical change
    grant: dict = None
    chance: float = 1.0      # for outings that can go wrong
    perm: dict = None        # permanent change to land quality


@dataclass
class Outing:
    key: str
    title: str
    hook: str                # why anyone is asking you
    outcomes: list           # first entry whose chance roll passes
    when: object = None
    weight: int = 2
    once: bool = False
    cooldown: int = 3


def _eff(target, mult, days, name, flavor):
    """A small, temporary change to what a piece of land can yield."""
    return {"type": "disaster", "name": name, "target": target,
            "efficiency_mult": mult, "duration": days,
            "recovery": "linear", "flavor": flavor}


def _stock(changes, name, flavor):
    return {"type": "stock", "name": name, "changes": changes, "flavor": flavor}


# Magnitudes here are deliberately small. A single outing moves a yield by a
# few percent for a few weeks -- under the week-to-week noise of the market.
# You are not supposed to be able to feel any one of these.
OUTINGS = [
    Outing(
        key="mine_goblins",
        title="Goblins in the north shaft",
        hook="The pit foreman has lost three men and will not send more down.",
        outcomes=[Outcome("You clear the shaft. The face is working again by Tuesday.",
                          pay=60,
                          event=_eff("ore_land", 1.05, 30, "North shaft reopened",
                                     "The north shaft is clear. Ore is moving."))],
    ),
    Outing(
        key="fire_ward",
        title="A hedge mage wants a fire ward tested",
        hook="She is confident. She is also standing a long way back.",
        outcomes=[
            Outcome("The ward holds. She writes something down and pays you.",
                    pay=75, chance=0.6),
            Outcome("The ward does not hold. Four acres of standing wheat go up.",
                    pay=75, perm={"grain_land": 0.985},
                    event=_eff("grain_land", 0.93, 45, "Fields burned",
                               "A hedge mage set fire to four acres. Nobody is saying how.")),
        ],
    ),
    Outing(
        key="boars",
        title="Boars in the orchards",
        hook="They have been at the roots. The farmers want them gone.",
        outcomes=[Outcome("You take four of them. The orchards are quiet.",
                          pay=45,
                          event=_eff("grain_land", 1.04, 25, "Boars culled",
                                     "The boars are dealt with."))],
    ),
    Outing(
        key="wasps",
        title="Smoke out the wasp nests in the cotton",
        hook="The pickers refuse to go near the south field.",
        outcomes=[
            Outcome("The nests burn out cleanly. The pickers go back.",
                    pay=40, chance=0.75,
                    event=_eff("cotton_land", 1.05, 25, "Wasps cleared",
                               "The wasps are gone from the south field.")),
            Outcome("The smoke takes the hedgerow with it, and a good deal of cotton.",
                    pay=40,
                    event=_eff("cotton_land", 0.92, 35, "South field burned",
                               "Half the south field went up with the wasps.")),
        ],
    ),
    Outing(
        key="collapsed_shaft",
        title="A collapsed shaft needs shoring",
        hook="Dull work, but nobody else will go down there.",
        outcomes=[Outcome("You get the props in. The seam is workable again.",
                          pay=55, perm={"ore_land": 1.02},
                          event=_eff("ore_land", 1.06, 35, "Shaft shored",
                                     "The old seam is open again."))],
    ),
    Outing(
        key="lost_lamb",
        title="A child has lost a lamb on the fell",
        hook="It is not worth your time and everyone knows it.",
        outcomes=[Outcome("You find the lamb. The child is delighted. That is all.",
                          pay=8)],
    ),
    Outing(
        key="ford_escort",
        title="See a corn factor safe to the ford",
        hook="Two days' easy riding with a nervous man and eight carts.",
        outcomes=[
            Outcome("You get him through. The carts unload at the granary.",
                    pay=70, chance=0.8,
                    event=_stock({"wheat": 120.0}, "Corn carts in",
                                 "Eight carts of corn came in over the ford.")),
            Outcome("You are jumped at the crossing. The carts are lost.",
                    pay=30,
                    event=_eff("grain_land", 0.96, 20, "Carts lost at the ford",
                               "The corn carts never made the ford.")),
        ],
    ),
    Outing(
        key="balverine",
        title="Something is taking sheep above the treeline",
        hook="The shepherds have a word for it and will not say it indoors.",
        outcomes=[
            Outcome("You kill it. The flocks come back down to the good grass.",
                    pay=140, chance=0.7,
                    event=_eff("cotton_land", 1.04, 30, "Beast killed",
                               "Whatever it was, it is dead. The flocks are back.")),
            Outcome("You do not kill it. You do not go back either.",
                    pay=0,
                    event=_eff("cotton_land", 0.95, 30, "Flocks kept in",
                               "The flocks are being kept in. Nobody will graze the high field.")),
        ],
    ),
    Outing(
        key="bandit_camp",
        title="A bandit camp on the ore road",
        hook="They have been taking one cart in three.",
        outcomes=[Outcome("You burn them out. The carters stop paying tolls.",
                          pay=110,
                          event=_eff("ore_land", 1.07, 40, "Ore road cleared",
                                     "The bandit camp is ash. The ore road is quiet."))],
    ),
    Outing(
        key="well",
        title="Something is fouling the village well",
        hook="Half the hamlet is sick and the harvest is standing.",
        outcomes=[Outcome("You clear it out. The reapers are back in the fields.",
                          pay=50,
                          event=_eff("grain_land", 1.05, 30, "Well cleared",
                                     "The well runs clean. The reapers are back."))],
    ),
    Outing(
        key="grey_seam",
        title="A seam of grey ore behind a warded door",
        hook="Old workings, older wards, and a rock that turns an iron pick.",
        when=lambda k: k.day > 50 and not k.town.goods["mithril_tool"].available,
        once=True,
        weight=5,
        outcomes=[Outcome("The assayer goes very quiet, then very loud. It is mithril.",
                          pay=90,
                          event=dict(QUESTS["bring_back_the_ore_sample"]["event"]))],
    ),
    Outing(
        key="summoning",
        title="A scholar wants a binding circle held open",
        hook="One hour, he says. He has not said what for.",
        outcomes=[
            Outcome("Whatever came through went back. He pays you well not to mention it.",
                    pay=160, chance=0.55),
            Outcome("It gets loose in the granary district before you put it down.",
                    pay=160, perm={"grain_land": 0.98},
                    event=_stock({"wheat": -220.0}, "Something in the granaries",
                                 "Something got into the granaries. Most of it is spoiled.")),
        ],
    ),
    Outing(
        key="weaver_debt",
        title="Collect a debt from a weaver who cannot pay",
        hook="The factor wants his money. The weaver wants another month.",
        outcomes=[
            Outcome("You take the looms. The factor pays. The weaver does not weave.",
                    pay=85, chance=0.5,
                    event=_eff("cotton_land", 0.94, 30, "Looms seized",
                               "They took the looms off a weaver in the lower town.")),
            Outcome("You pay it yourself and say nothing.", pay=-60),
        ],
    ),
    Outing(
        key="blight_herbs",
        title="Blightbane from the high meadows",
        hook="An alchemist swears it turns the rot in standing corn.",
        when=lambda k: k.day > 30,
        outcomes=[Outcome("She was right, more or less. The fields hold.",
                          pay=95,
                          event=_eff("grain_land", 1.08, 35, "Blight treated",
                                     "The alchemist's wash is working on the corn."))],
    ),
    Outing(
        key="smelter",
        title="The smelter has gone cold and nobody knows why",
        hook="Something in the flue, they think. Something with teeth.",
        outcomes=[Outcome("You get it out. The smelter is lit by evening.",
                          pay=70,
                          event=_eff("ore_land", 1.05, 28, "Smelter relit",
                                     "The smelter is running again."))],
    ),
    Outing(
        key="cattle_raid",
        title="Ride with a lord's men on a cattle raid",
        hook="It is his cattle, he says. The other lord says otherwise.",
        outcomes=[Outcome("You come back with cattle and a small share of them.",
                          pay=120, grant={"wheat": 40.0},
                          event=_eff("grain_land", 0.96, 25, "Border raiding",
                                     "There is raiding along the border again. Nobody is sowing."))],
    ),
    Outing(
        key="rats",
        title="Rats in the corn stores",
        hook="Tedious, thankless, and somebody has to.",
        outcomes=[Outcome("You clear the stores. Less is lost this month.",
                          pay=30,
                          event=_stock({"wheat": 60.0}, "Stores cleared",
                                       "Somebody finally did something about the rats."))],
    ),
    Outing(
        key="storm_ward",
        title="Hold a weather ward over the harvest",
        hook="Three days standing in a field in the rain, for good money.",
        outcomes=[
            Outcome("The ward holds and the harvest comes in dry.",
                    pay=130, chance=0.65,
                    event=_eff("grain_land", 1.07, 30, "Harvest saved",
                               "The rain went round the valley. Somebody paid for that.")),
            Outcome("You lose the ward on the second night. The harvest is flattened.",
                    pay=130,
                    event=_eff("grain_land", 0.90, 40, "Harvest flattened",
                               "The storm came straight through. Half the corn is flat.")),
        ],
    ),
    Outing(
        key="marsh",
        title="Drain the marsh below the mill",
        hook="Weeks of filthy work. There is good black soil under it.",
        weight=1, cooldown=8,
        outcomes=[Outcome("The ditches hold. That is arable land now, for good.",
                          pay=90, perm={"grain_land": 1.03},
                          event=_stock({"wheat": 40.0}, "Marsh drained",
                                       "They have drained the marsh below the mill."))],
    ),
    Outing(
        key="mill_wheel",
        title="The mill wheel has cracked its axle",
        hook="Nobody within thirty miles can lift it but you.",
        weight=1, cooldown=8,
        outcomes=[Outcome("The wheel turns again, and better than before.",
                          pay=65, perm={"grain_land": 1.025},
                          event=_stock({"wheat": 60.0}, "Mill repaired",
                                       "The mill is turning again."))],
    ),
    Outing(
        key="charcoal",
        title="A charcoal burner wants the old wood cleared",
        hook="Good money. It is a very old wood.",
        weight=1, cooldown=8,
        outcomes=[Outcome("The wood comes down. The smelters have fuel for years.",
                          pay=120, perm={"ore_land": 1.03, "cotton_land": 0.98},
                          event=_eff("ore_land", 1.06, 30, "Charcoal for the smelters",
                                     "There is charcoal enough to run the smelters hot."))],
    ),
]


class Board:
    """What is being asked of you this outing."""

    def __init__(self, rng):
        self.rng = rng
        self.spent = set()
        self.last_seen = {}

    def offer(self, kingdom, turn, count=2):
        pool = []
        for o in OUTINGS:
            if o.key in self.spent:
                continue
            if turn - self.last_seen.get(o.key, -99) < o.cooldown:
                continue
            if o.when is not None and not o.when(kingdom):
                continue
            pool.extend([o] * max(1, o.weight))
        picked, seen = [], set()
        self.rng.shuffle(pool)
        for o in pool:
            if o.key in seen:
                continue
            seen.add(o.key)
            picked.append(o)
            if len(picked) == count:
                break
        return picked

    def taken(self, outing, turn):
        self.last_seen[outing.key] = turn
        if outing.once:
            self.spent.add(outing.key)


def resolve(kingdom, outing, tick):
    """Do the outing. Some of them do not go the way anyone intended."""
    bonus = kingdom.gear_bonus()
    outcome = outing.outcomes[-1]
    for candidate in outing.outcomes:
        chance = candidate.chance
        if chance < 1.0:
            chance = min(0.95, chance + bonus)   # better kit, fewer accidents
        if chance >= 1.0 or kingdom.rng.random() < chance:
            outcome = candidate
            break

    mult = kingdom.wage_multiplier()
    pay = outcome.pay * mult if outcome.pay > 0 else outcome.pay
    kingdom.player.gold += pay
    kingdom.earned += max(0.0, pay)
    kingdom.wage_log.append(mult)
    kingdom.last_pay = pay
    if outcome.grant:
        for key, qty in outcome.grant.items():
            kingdom.player.inventory[key] = kingdom.player.holding(key) + qty

    if outcome.perm:
        # Straight into the town's land quality, where nothing expires.
        for resource, mult in outcome.perm.items():
            current = kingdom.town.efficiency_bonus.get(resource, 1.0)
            kingdom.town.efficiency_bonus[resource] = current * mult
        kingdom.footprints.append(
            (tick, outing.title,
             {"type": "permanent", "changes": dict(outcome.perm)}))

    if outcome.event:
        event = dict(outcome.event)
        kingdom.footprints.append((tick, outing.title, event))
        if event["type"] == "stock":
            for key, delta in event["changes"].items():
                good = kingdom.town.goods[key]
                good.inventory = max(0.0, good.inventory + delta)
            kingdom.town.event_log.append((tick, event["name"], event.get("flavor", "")))
        else:
            kingdom.town.trigger(event, tick)
        if event.get("flavor"):
            kingdom.rumours.on_quest(tick + 2, event["flavor"])

    kingdom.jobs_taken.append((tick, outing.title))
    return outcome


# --------------------------------------------------------------------------
# being away from town
# --------------------------------------------------------------------------

def hearsay(kingdom, good_key, rng):
    """What you hear about a price on the road. Late, and roughly right.

    You are out in the hills. You do not have a price board. What you have is
    whatever the last carter told you, which was true a week ago and has been
    through two retellings since.
    """
    hist = kingdom.town.history["price"][good_key]
    if len(hist) < 8:
        return None
    # How far back the carter's information is. Never further back than the
    # town has existed -- the guard used to be a flat length check while the
    # lookback went to 12, so any run that asked for hearsay on day 10 walked
    # off the end of the list.
    back = rng.randint(6, min(12, len(hist)))
    stale = hist[-back]
    fuzz = stale * rng.uniform(0.88, 1.12)
    return fuzz


def road_talk(kingdom, rng):
    goods = [g for g in kingdom.town.active_goods()]
    if not goods:
        return None
    g = rng.choice(goods)
    heard = hearsay(kingdom, g.key, rng)
    if heard is None:
        return None
    real = g.price
    phrasing = rng.choice([
        "A carter says %s was fetching about %.1fg at market when he came through.",
        "You hear %s is around %.1fg in town, though that was some days back.",
        "A pedlar reckons %s is going for %.1fg. He may be talking it up.",
    ])
    line = phrasing % (g.name.lower(), heard)
    off = abs(heard - real) / max(real, 1e-6) * 100
    return line, off


# --------------------------------------------------------------------------
# the year
# --------------------------------------------------------------------------

def ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(0)


def show_town(kingdom, day):
    print()
    print("  " + "-" * 62)
    print("  YOU RIDE INTO %s      day %d" % (kingdom.town.name.upper(), day))
    print("  " + "-" * 62)
    print("  %-14s %9s %11s" % ("", "price", "vs a year ago"))
    for g in kingdom.town.active_goods():
        drift = (g.price / g.base_price - 1.0) * 100.0
        print("  %-14s %9.2fg %10.0f%%" % (g.name, g.price, drift))
    print("  %-14s %9.0fg" % ("your purse", kingdom.player.gold))
    print("  %-14s %9s   work pays %+.0f%% here" % (
        "carrying", GEAR[kingdom.gear]["name"], (kingdom.wage_multiplier() - 1) * 100))


def visit_smith(kingdom, day, policy):
    """If the town has found a better metal, the smith will sell you kit in it."""
    if kingdom.gear != "iron":
        return
    price = kingdom.gear_price("mithril")
    if price is None:
        return
    print()
    print("  The smith has mithril kit on the bench. %.0fg, at today's price." % price)
    print("  It would make the dangerous work go wrong less often.")
    if policy == "ask":
        answer = ask("  Buy it? (y/n)  > ")
        want = answer.lower().startswith("y")
    else:
        # a sensible adventurer buys it once it is comfortably affordable
        want = kingdom.player.gold >= price * 2.0
        print("  > %s" % ("yes" if want else "not yet"))
    if want:
        res = kingdom.buy_gear("mithril", day)
        print("  >>  %s" % ("bought, %.0fg" % res["price"] if res["ok"] else res["reason"]))


POLICIES = {
    "ask": "you decide each outing",
    "first": "always take the first thing offered",
    "second": "always take the second",
    "random": "take whichever, without much thought",
}


def choose_policy():
    print("  How do you want to ride this year out?")
    print()
    print("    1) Decide each outing yourself")
    print("    2) Always take the first thing offered, and let the year run")
    print("    3) Always take the second, and let the year run")
    print("    4) Take whichever, without much thought")
    print()
    while True:
        answer = ask("  > ")
        if answer.lower() in ("quit", "q"):
            raise SystemExit(0)
        pick = {"1": "ask", "2": "first", "3": "second", "4": "random",
                "": "ask"}.get(answer)
        if pick:
            return pick
        print("  ..  pick 1, 2, 3 or 4")


def play(kingdom, days, outing_days=5, town_every=5, policy="ask"):
    board = Board(kingdom.rng)
    print()
    print("  You are an adventurer. You take work, you ride out, you come")
    print("  back. Nobody is asking you to think about the price of bread.")
    print()
    if policy == "ask":
        print("  Pick 1 or 2 each time. Enter alone takes the first.")
    else:
        print("  Riding out on: %s." % POLICIES[policy])

    day, turn = 0, 0
    while day < days:
        turn += 1
        offers = board.offer(kingdom, turn, 2)
        if not offers:
            offers = [OUTINGS[0]]

        print()
        print("  --- outing %d, day %d %s" % (turn, day, "-" * 34))
        if policy == "ask":
            for i, o in enumerate(offers, 1):
                print("    %d) %-38s %s" % (i, o.title, o.hook))

        if policy == "ask":
            while True:
                answer = ask("  > ")
                if answer.lower() in ("quit", "q"):
                    return day
                if answer == "":
                    pick = 1
                    break
                try:
                    pick = int(answer)
                    if 1 <= pick <= len(offers):
                        break
                except ValueError:
                    pass
                print("  ..  pick 1 or 2")
        elif policy == "first":
            pick = 1
        elif policy == "second":
            pick = min(2, len(offers))
        else:
            pick = kingdom.rng.randint(1, len(offers))

        chosen = offers[pick - 1]
        outcome = resolve(kingdom, chosen, day)
        board.taken(chosen, turn)
        if policy != "ask":
            print("      %s" % chosen.title)
        print("      %s" % outcome.text)
        if outcome.pay:
            mult = kingdom.wage_log[-1] if kingdom.wage_log else 1.0
            note = ""
            if outcome.pay > 0 and abs(mult - 1.0) >= 0.05:
                note = "  (%s rates, %+.0f%%)" % (
                    "good" if mult > 1 else "poor", (mult - 1) * 100)
            print("      %+.0fg%s" % (kingdom.last_pay, note))

        # the days pass while you are out in the hills
        for _ in range(outing_days):
            if day >= days:
                break
            kingdom.step(day)
            kingdom.whats_being_said(day)   # you are not in town to hear it
            day += 1

        talk = road_talk(kingdom, kingdom.rng)
        if talk:
            print("      .. %s" % talk[0])

        if turn % town_every == 0:
            show_town(kingdom, day)
            visit_smith(kingdom, day, policy)

    return day


# --------------------------------------------------------------------------
# what all that adventuring actually did
# --------------------------------------------------------------------------

def counterfactual(config, seed, days, footprints, outing_days):
    """Replay the same year with the player's marks taken out.

    This is the only honest way to answer the question. Every scheduled world
    event still fires, the same seed drives the same producers, the foreign
    market runs off its own stream -- the single difference is that you were
    never there.
    """
    ghost = Kingdom(config, seed=seed)
    day = 0
    while day < days:
        for _ in range(outing_days):
            if day >= days:
                break
            ghost.step(day)
            ghost.whats_being_said(day)
            day += 1
    return ghost


def settle_up(kingdom, days, config, seed, outing_days, policy="ask", asked_days=None):
    town = kingdom.town
    print()
    print("=" * 66)
    print("  A YEAR LATER")
    print("=" * 66)
    print("  %-26s %10.0fg" % ("you rode in with", kingdom.starting_worth))
    print("  %-26s %10.0fg" % ("you are worth now",
                               kingdom.player.net_worth(town)))
    print("  %-26s %10d" % ("outings taken", len(kingdom.jobs_taken)))
    print("  %-26s %10d" % ("that left a mark", len(kingdom.footprints)))
    if kingdom.wage_log:
        avg = sum(kingdom.wage_log) / len(kingdom.wage_log)
        print("  %-26s %10.0fg  at %+.0f%% on ordinary rates" % (
            "earned from work", kingdom.earned, (avg - 1) * 100))
    print("  %-26s %10s" % ("carrying", GEAR[kingdom.gear]["name"]))
    print()
    print("  %-26s %10d" % ("this year was seed", seed))
    again = "python kingdom.py --seed %d --days %d" % (seed, asked_days or days)
    if policy != "ask":
        again += " --policy %s" % policy
    print("  ride it again:  %s" % again)

    print()
    print("  Nobody asked you to change the price of anything.")
    print("  Here is the same year with you taken out of it.")
    print()
    ghost = counterfactual(config, seed, days, kingdom.footprints, outing_days)
    print("  %-14s %11s %11s %11s" % ("", "with you", "without you", "difference"))
    print("  " + "-" * 52)
    for g in town.active_goods():
        mine = g.price
        theirs = ghost.town.goods[g.key].price
        if theirs < 1e-6:
            continue
        gap = (mine / theirs - 1.0) * 100.0
        print("  %-14s %10.2fg %10.2fg %10.0f%%" % (g.name, mine, theirs, gap))

    # The prices are the surface, and the surface mostly springs back: shift
    # the supply of something and the producers just move land around until
    # revenue per acre is level again. What does NOT spring back is the land.
    changed = {r: v for r, v in town.efficiency_bonus.items()
               if abs(v - 1.0) > 0.005}
    if changed:
        print()
        print("  Prices settle back. The ground does not. What you did to the")
        print("  county itself, and nothing will undo:")
        for resource, mult in sorted(changed.items()):
            drift = (mult - 1.0) * 100.0
            bar = "#" * min(24, int(abs(drift) * 1.5) + 1)
            print("    %-10s %+6.1f%% %s %s" % (
                resource.split("_")[0], drift,
                "better" if drift > 0 else "worse ", bar))

    def tail(series, n=40):
        return sum(series[-n:]) / max(len(series[-n:]), 1) if series else 0.0

    mine_food = tail(town.history["real_consumption"])
    ghost_food = tail(ghost.town.history["real_consumption"])
    if ghost_food:
        print()
        print("  %-30s %8.2f" % ("goods per head, with you", mine_food))
        print("  %-30s %8.2f  (%+.1f%%)" % (
            "goods per head, without you", ghost_food,
            (mine_food / ghost_food - 1) * 100))

    # Which outings pushed hardest on what. Modifier-days is an honest
    # measure of pressure applied, not a causal decomposition -- the market
    # is far too tangled for that -- but it says where the pressure came from.
    print()
    print("  Where that came from (how hard each outing pushed, and on what):")
    pressure = {}
    for _tick, title, event in kingdom.footprints:
        kind = event["type"]
        if kind == "stock":
            for key, delta in event["changes"].items():
                pressure[(title, key)] = pressure.get((title, key), 0.0) + delta / 100.0
        elif kind == "disaster":
            score = (event["efficiency_mult"] - 1.0) * event["duration"]
            key = (title, event["target"])
            pressure[key] = pressure.get(key, 0.0) + score
        elif kind == "demand_shock":
            for good_key, mult in event.get(
                    "targets", {event.get("target"): event.get("demand_mult", 1.0)}).items():
                score = (mult - 1.0) * event.get("duration", 20)
                pressure[(title, good_key)] = pressure.get((title, good_key), 0.0) + score
        elif kind == "permanent":
            for resource, mult in event["changes"].items():
                key = (title + " (permanent)", resource)
                pressure[key] = pressure.get(key, 0.0) + (mult - 1.0) * 400
        elif kind == "discovery":
            key = (title, event["introduces"])
            pressure[key] = pressure.get(key, 0.0) + 40.0
    ranked = sorted(pressure.items(), key=lambda kv: -abs(kv[1]))[:8]
    if not ranked:
        print("    You left nothing behind at all.")
        return
    biggest = max(abs(v) for _, v in ranked) or 1.0
    for (title, target), score in ranked:
        bar = "#" * max(1, int(abs(score) / biggest * 24))
        arrow = "more" if score > 0 else "less"
        label = title if len(title) <= 40 else title[:37] + "..."
        print("    %-42s %-9s %s %s" % (
            label, target.split("_")[0], arrow, bar))
    print()


def main():
    ap = argparse.ArgumentParser(
        description="An adventurer, a county, and a year of small consequences.")
    ap.add_argument("--config", default="world.json")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--outing", type=int, default=5, help="days an outing takes")
    ap.add_argument("--town-every", type=int, default=5, help="outings between town visits")
    ap.add_argument("--seed", type=int, default=None,
                    help="ride a particular year again; omit for a new one")
    ap.add_argument("--policy", choices=sorted(POLICIES),
                    help="skip the opening question and just ride")
    args = ap.parse_args()

    with open(args.config, "r") as f:
        config = json.load(f)

    seed = args.seed if args.seed is not None else random.randint(1, 999999)
    kingdom = Kingdom(config, seed=seed)
    kingdom.starting_worth = kingdom.player.net_worth(kingdom.town)
    print("=" * 66)
    print("  A YEAR IN THE COUNTY OF %s" % kingdom.town.name.upper())
    print("=" * 66)
    policy = args.policy or choose_policy()
    try:
        played = play(kingdom, args.days, args.outing, args.town_every, policy)
    except SystemExit:
        played = kingdom.day
    settle_up(kingdom, played, config, seed, args.outing, policy, args.days)


if __name__ == "__main__":
    main()
