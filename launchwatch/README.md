# launchwatch

Real-time monitor for your own token launch, plus the playbook it belongs to.

- [`PLAYBOOK.md`](PLAYBOOK.md) — the Clean Launch Playbook: how to launch a pure
  memecoin with no promises, without becoming the villain.
- [`launchwatch.py`](launchwatch.py) — the monitor.

## What the monitor does

It subscribes to the [PumpPortal](https://pumpportal.fun) trade feed for a single
mint and journals **every** trade to a local SQLite database (`launch.db`). From
that it derives, on a 30-second tick:

| Readout | Why it matters |
| --- | --- |
| bonding curve % | progress toward graduation |
| holder count | real distribution |
| top-10 held % | one exit can halve the price |
| sniper wallets | wallets that bought in the first 30s, what they still hold, and the SOL they've extracted |
| 60s flow | buy vs sell pressure |
| **creator wallet** | `CLEAN — no creator sells`, or the exact amount sold |

That last line is the point. The journal is evidence you can publish at any
moment to show you are not selling into your own buyers.

It is **read-only** — it observes a public feed and writes to a local file. It
does not hold keys, submit transactions, or trade.

## Usage

```sh
pip install -r requirements.txt

# watch a live launch — start this before you announce the mint
python launchwatch.py <MINT_ADDRESS> <YOUR_CREATOR_WALLET>

# replay / report from a previous session
python launchwatch.py report <MINT_ADDRESS>
```

Run with no arguments to print usage.

`Ctrl-C` stops the monitor and prints the final report. Every trade is committed
as it arrives, so a hard kill cannot lose the tail of the journal.

## Notes and caveats

- **Elapsed time is measured from process start, not from the mint.** The 30-second
  sniper window is only meaningful if you start the monitor *before* the first
  trade. Start it from block 0.
- `GRADUATION_SOL` (85.0) is the approximate pump.fun migration threshold. Verify
  it against current docs before relying on the curve percentage.
- Sniper holdings are reported both as a share of circulating supply (the tokens
  the monitor has actually seen trade) and as a share of `TOTAL_SUPPLY`.
- Reconnects are automatic with exponential backoff up to 60s. Holder state
  survives a reconnect; it does not survive a restart.
