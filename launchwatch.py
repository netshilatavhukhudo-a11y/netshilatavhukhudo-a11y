#!/usr/bin/env python3
"""
launchwatch.py — real-time monitor for YOUR OWN token launch.

Watches the bonding curve, holder concentration, sniper wallets, and — most
importantly — publicly logs your creator wallet's activity so you can prove
you aren't selling. That proof is the entire product of an honest launch.

    pip install websockets
    python launchwatch.py <MINT_ADDRESS> <YOUR_CREATOR_WALLET>

    # replay / report from a previous session (no websockets needed)
    python launchwatch.py report <MINT_ADDRESS>
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
DB_PATH = "launch.db"

# Graduation threshold — pump.fun migrates at roughly this much SOL in the
# curve. Verify against the current docs before you rely on the % readout.
GRADUATION_SOL = 85.0

SNIPER_WINDOW_SECONDS = 30    # buys inside this window are treated as snipes
CONCENTRATION_ALERT = 0.35    # alert if top 10 wallets hold >35% of supply
FLOW_WINDOW_SECONDS = 60      # rolling window for buy/sell pressure

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("launch")


@dataclass
class Holder:
    wallet: str
    balance: float = 0.0
    sol_in: float = 0.0
    sol_out: float = 0.0
    first_seen: float = -1.0   # seconds after monitor start; -1 = never traded
    buys: int = 0
    sells: int = 0

    @property
    def is_sniper(self) -> bool:
        return 0 <= self.first_seen <= SNIPER_WINDOW_SECONDS

    @property
    def realised_sol(self) -> float:
        return self.sol_out - self.sol_in

    @property
    def exited(self) -> bool:
        return self.balance <= 0 and self.sells > 0


def open_db(path: str | None = None) -> sqlite3.Connection:
    db = sqlite3.connect(path if path is not None else DB_PATH)
    # WAL keeps per-trade commits cheap, so a Ctrl+C or a crash mid-launch
    # never costs us the journal we are running the whole thing for.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, ts TEXT,
        elapsed REAL, wallet TEXT, side TEXT, sol REAL, tokens REAL,
        mcap_sol REAL, is_creator INTEGER, is_sniper INTEGER)""")
    db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, ts TEXT,
        elapsed REAL, mcap_sol REAL, curve_pct REAL, holders INTEGER,
        top10_pct REAL, sniper_held_pct REAL, creator_sold_sol REAL)""")
    db.commit()
    return db


class LaunchMonitor:
    def __init__(self, mint: str, creator: str):
        self.mint = mint
        self.creator = creator
        self.start = time.time()

        self.holders: dict[str, Holder] = {}
        self.mcap_sol = 0.0
        self.curve_sol = 0.0
        self.flow: deque = deque()          # (ts, side, sol_amount)
        self.alerts_fired: set[str] = set()

        self.creator_sold_sol = 0.0
        self.creator_sell_events = 0

        self._ticker_task: asyncio.Task | None = None
        self.db = open_db()

    # -- ingest ------------------------------------------------------------
    def on_trade(self, msg: dict):
        wallet = msg.get("traderPublicKey", "")
        side = msg.get("txType", "")
        sol = abs(float(msg.get("solAmount") or 0))
        tokens = abs(float(msg.get("tokenAmount") or 0))
        elapsed = time.time() - self.start

        self.mcap_sol = float(msg.get("marketCapSol") or self.mcap_sol)
        self.curve_sol = float(msg.get("vSolInBondingCurve") or self.curve_sol)

        h = self.holders.get(wallet)
        if not h:
            h = Holder(wallet=wallet, first_seen=elapsed)
            self.holders[wallet] = h
        # A missing newTokenBalance means "unreported", not "sold out" — keep
        # the last known balance rather than silently zeroing a live holder.
        if msg.get("newTokenBalance") is not None:
            h.balance = float(msg["newTokenBalance"])

        if side == "buy":
            h.sol_in += sol
            h.buys += 1
        else:
            h.sol_out += sol
            h.sells += 1
            if wallet == self.creator:
                self.creator_sold_sol += sol
                self.creator_sell_events += 1
                log.error("!! CREATOR WALLET SOLD %.3f SOL — this is now on "
                          "chain forever and people will find it", sol)

        self.flow.append((time.time(), side, sol))
        cutoff = time.time() - FLOW_WINDOW_SECONDS
        while self.flow and self.flow[0][0] < cutoff:
            self.flow.popleft()

        self.db.execute(
            "INSERT INTO events (mint, ts, elapsed, wallet, side, sol, tokens,"
            " mcap_sol, is_creator, is_sniper) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.mint, datetime.now(timezone.utc).isoformat(), elapsed,
             wallet, side, sol, tokens, self.mcap_sol,
             int(wallet == self.creator), int(self.is_sniper(h))))
        self.db.commit()

    # -- derived metrics ---------------------------------------------------
    def is_sniper(self, h: Holder) -> bool:
        # Your own dev buy lands in the same block as the mint, so it trips the
        # first-30s test. Counting it as a snipe would inflate every sniper
        # number you publish — and those numbers are the point of this tool.
        return h.is_sniper and h.wallet != self.creator

    def active_holders(self) -> list[Holder]:
        return [h for h in self.holders.values() if h.balance > 0]

    def curve_pct(self) -> float:
        return min(self.curve_sol / GRADUATION_SOL * 100, 100.0)

    def top10_pct(self) -> float:
        active = self.active_holders()
        top = sorted(active, key=lambda h: -h.balance)[:10]
        held = sum(h.balance for h in top)
        total = sum(h.balance for h in active) or 1
        return held / total * 100

    def sniper_stats(self) -> tuple[int, float, float]:
        """(count, % of circulating still held by snipers, SOL they extracted)"""
        snipers = [h for h in self.holders.values() if self.is_sniper(h)]
        total = sum(h.balance for h in self.active_holders()) or 1
        held = sum(h.balance for h in snipers if h.balance > 0)
        extracted = sum(h.realised_sol for h in snipers if h.realised_sol > 0)
        return len(snipers), held / total * 100, extracted

    def flow_ratio(self) -> tuple[float, float]:
        buys = sum(s for _, side, s in self.flow if side == "buy")
        sells = sum(s for _, side, s in self.flow if side == "sell")
        return buys, sells

    # -- alerts ------------------------------------------------------------
    def check_alerts(self):
        def fire(key, msg, level=log.warning):
            if key not in self.alerts_fired:
                self.alerts_fired.add(key)
                level(msg)

        _, sniper_pct, extracted = self.sniper_stats()
        if sniper_pct > 25:
            fire("snipers", f"~{sniper_pct:.0f}% of supply is held by wallets "
                            f"that bought in the first {SNIPER_WINDOW_SECONDS}s. "
                            f"They will exit into your community.")
        if self.top10_pct() > CONCENTRATION_ALERT * 100:
            fire("concentration",
                 f"top 10 wallets hold {self.top10_pct():.0f}% — one exit "
                 f"can halve the price")
        if extracted > 5:
            fire("extraction", f"snipers have taken ~{extracted:.1f} SOL out "
                               f"of your buyers so far")
        for milestone in (25, 50, 75, 100):
            if self.curve_pct() >= milestone:
                fire(f"curve{milestone}",
                     f"bonding curve {milestone}% complete", log.info)

    # -- output ------------------------------------------------------------
    def snapshot(self):
        active = self.active_holders()
        n_snipers, sniper_pct, extracted = self.sniper_stats()
        buys, sells = self.flow_ratio()
        elapsed = time.time() - self.start

        self.db.execute(
            "INSERT INTO snapshots (mint, ts, elapsed, mcap_sol, curve_pct,"
            " holders, top10_pct, sniper_held_pct, creator_sold_sol)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.mint, datetime.now(timezone.utc).isoformat(), elapsed,
             self.mcap_sol, self.curve_pct(), len(active), self.top10_pct(),
             sniper_pct, self.creator_sold_sol))
        self.db.commit()

        creator_line = ("CLEAN — no creator sells"
                        if self.creator_sell_events == 0
                        else f"SOLD {self.creator_sold_sol:.3f} SOL "
                             f"({self.creator_sell_events} txs)")

        print(f"\n{'─' * 58}")
        print(f" {int(elapsed // 60):>3}m{int(elapsed % 60):02d}s   "
              f"mcap {self.mcap_sol:>7.1f} SOL   curve {self.curve_pct():>5.1f}%")
        print(f"{'─' * 58}")
        print(f"  holders          {len(active):>6}")
        print(f"  top-10 held      {self.top10_pct():>5.1f}%")
        print(f"  sniper wallets   {n_snipers:>6}  holding {sniper_pct:.1f}%  "
              f"extracted {extracted:.1f} SOL")
        print(f"  flow ({FLOW_WINDOW_SECONDS}s)     "
              f"+{buys:.2f} / -{sells:.2f} SOL")
        print(f"  creator wallet   {creator_line}")
        print(f"{'─' * 58}")

    async def ticker(self, every: int = 30):
        while True:
            await asyncio.sleep(every)
            self.snapshot()
            self.check_alerts()

    async def run(self):
        try:
            import websockets
        except ImportError:
            sys.exit("websockets is not installed — run: pip install websockets")

        print(f"\nwatching {self.mint}")
        print(f"creator  {self.creator}")
        print(f"every trade is being journalled to {DB_PATH}\n")

        # Keep a reference: a bare create_task() can be garbage collected
        # mid-launch, which silently stops every snapshot and alert.
        self._ticker_task = asyncio.create_task(self.ticker())
        backoff = 1
        while True:
            try:
                async with websockets.connect(PUMPPORTAL_WS, ping_interval=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade",
                                              "keys": [self.mint]}))
                    backoff = 1
                    log.info("connected")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(msg, dict) and msg.get("mint") == self.mint:
                            if msg.get("txType") in ("buy", "sell"):
                                self.on_trade(msg)
            except Exception as e:
                log.warning("disconnected (%s), retry in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def close(self):
        if self._ticker_task is not None:
            self._ticker_task.cancel()
        self.db.close()


def report(mint: str):
    db = open_db()
    try:
        row = db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT wallet), "
            "SUM(CASE WHEN side='buy' THEN sol ELSE 0 END), "
            "SUM(CASE WHEN side='sell' THEN sol ELSE 0 END), "
            "SUM(CASE WHEN is_creator=1 AND side='sell' THEN sol ELSE 0 END) "
            "FROM events WHERE mint=?", (mint,)).fetchone()
        n, wallets, bought, sold, creator_sold = row
        if not n:
            print("no events recorded for that mint")
            return

        creator_sold = creator_sold or 0
        print(f"\nLaunch report — {mint}")
        print(f"  trades            {n}")
        print(f"  unique wallets    {wallets}")
        print(f"  SOL in            {bought or 0:.2f}")
        print(f"  SOL out           {sold or 0:.2f}")
        print(f"  creator sold      {creator_sold:.3f} SOL", end="")
        print("   <- publish this number" if not creator_sold
              else "   <- this will be found")

        print(f"\n  sniper cohort (first {SNIPER_WINDOW_SECONDS}s):")
        for w, sol_in, sol_out, _buys, _sells in db.execute(
                "SELECT wallet, SUM(CASE WHEN side='buy' THEN sol ELSE 0 END),"
                " SUM(CASE WHEN side='sell' THEN sol ELSE 0 END),"
                " SUM(side='buy'), SUM(side='sell') FROM events"
                " WHERE mint=? AND is_sniper=1 GROUP BY wallet"
                " ORDER BY 3 DESC LIMIT 10", (mint,)):
            pnl = (sol_out or 0) - (sol_in or 0)
            print(f"    {w[:10]}…  in {sol_in or 0:>6.2f}  out {sol_out or 0:>6.2f}"
                  f"  net {pnl:>+7.2f} SOL")
        print()
    finally:
        db.close()


def main(argv: list[str]) -> None:
    if len(argv) >= 3 and argv[1] == "report":
        report(argv[2])
    elif len(argv) >= 3:
        monitor = LaunchMonitor(argv[1], argv[2])
        try:
            asyncio.run(monitor.run())
        except KeyboardInterrupt:
            print()
        finally:
            monitor.close()
            report(argv[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
