# Clean Launch Playbook

For launching a pure memecoin with no promises, without becoming the villain.

> Nothing here is legal or financial advice. Figures quoted below were accurate
> when this was written and change often — verify them against current sources
> before you rely on any of them.

---

## 1. Change where your money comes from

This is the whole thing. There are two ways a launcher makes money:

**Selling your bag.** You hold supply, the price goes up, you sell into your
buyers. Every rand you make is a rand someone else lost. If you did it
deliberately after promoting the coin, that's a rug — and it's the thing that
ends careers.

**Creator fee share.** Pump.fun pays the creator a percentage of the fees on
every trade of their token. The largest share, 0.95% per trade, applies to
tokens between roughly $88,000 and $300,000 market cap, scaling down to 0.05%
around $20 million. You earn from volume, not from exiting.

Take the second one and your incentives flip completely. You now want the coin
alive, traded, and held — the exact opposite of a rug. You can say publicly
"I make money only if this stays alive" and it will be true and verifiable.

**Set your target as fee revenue, not bag value. Write the number down before
you launch.**

### The honest expected value

Don't romanticise it. SolanaFloor's data on creator earnings found only 1.8% of
creators earned between $5,000 and $10,000, while 48% earned between $100 and
$1,000, and around 35% earned under $100. Roughly 98.6% of tokens launched on
the platform never make it.

Median outcome for a first launch with no existing audience: under R2,000 in
fees, and the coin is dead in 48 hours. Plan around that number. If it beats it,
great.

---

## 2. The problem nobody warns you about

You can run a perfectly honest launch and your community still gets robbed — by
third parties, using your coin as the vehicle.

Sniper bots watch every new mint and buy in the first block. They take a large
chunk of supply at the lowest price on the curve. Then they wait for you to
bring in real buyers, and they sell into them.

Run `launchwatch.py` against a simulated launch and you see the shape of it:

```
  sniper wallets   6   holding 0.0%   extracted 21.0 SOL
  creator wallet   CLEAN — no creator sells
```

Six bots put in 12 SOL and took out 33. Thirty real buyers put in 24 SOL and are
holding the bag. You did nothing wrong and your community still got extracted
from. Then they blame you, because your name is on it.

This is why 98% fail. Not because the meme was bad — because the early supply
was never in community hands to begin with.

Mitigations, in order of usefulness:

- Launch on a pad with sniper protection / delayed-trading options and actually
  enable them
- Make a meaningful dev buy in the same transaction as the mint, transparently
  disclosed, so bots aren't taking the whole first tranche
- Don't announce the mint address before it's live. Announce after the first
  minutes of bot activity has flushed through
- Watch `launchwatch.py` live and tell your community honestly what you're
  seeing

That last one is worth more than it sounds. "Six bot wallets took 18% of supply
in the first block, here are the addresses, be careful" buys you more long-term
credibility than any pump ever will.

---

## 3. The transparency protocol

Publish these before launch, in writing, where people can screenshot them:

1. **Your creator wallet address.** One wallet. Public. `launchwatch.py` logs
   every sell from it; the report prints `creator sold 0.000 SOL` and you can
   post that at any point as proof.
2. **Exactly what you hold and what you paid for it.**
3. **Your sell policy.** The honest version is "I don't sell, I earn from
   creator fees." If you do intend to sell some, say the amount and the timing
   before launch, then do exactly that and nothing more.
4. **What this is.** "This is a memecoin. It has no utility, no roadmap, no
   team, and no promises. It will probably go to zero. Do not put in money you
   need."

That last one feels like it kills the launch. It doesn't — it's the only thing
that separates you from the thousand identical launches that day, and it's the
thing that protects you legally, because you promised nothing.

**Never:** bundle supply into wallets you control, run volume bots, fake holder
counts, use fresh wallets to simulate demand, or say anything resembling "this
is going to 100x." The first four are fraud. The fifth is what turns a legal
memecoin into an unregistered securities problem.

---

## 4. Practical sequence

### Before (a week out)

- Ticker, art, one-line meme. It has to be legible in under two seconds.
- Fresh dedicated wallet. Not your main, not one linked to DOLLARSHARK funds.
- Write the four disclosures above and pin them.
- Decide your dev buy size — money you would genuinely shrug off losing
  entirely.
- Set up a Telegram or X account for the coin, separate from your brand
  accounts.

### Launch hour

- Mint. Dev buy in the same tx, disclosed.
- Start `python launchwatch.py <MINT> <YOUR_WALLET>` immediately — it needs to
  be running from block 0 to catch the sniper cohort.
- Do not announce to your PipSense/FacelessOS lists. Seriously. Keep the coin
  and the businesses separate — if the coin dies, and it probably will, you want
  zero blast radius on the thing that pays you.

### First 48 hours

- This is a full-time job. Constant presence, replying, memeing. If you're not
  online, it dies. That's not motivational — it's the actual mechanic.
- Post the `launchwatch` numbers periodically. Good or bad.
- When it fades, say so honestly and let it go. Do not "revive" it by pumping.

### After

- Claim your creator fees. That's your revenue.
- Run `python launchwatch.py report <MINT>` and keep the record.

---

## 5. Scheduling reality

That 48-hour window is non-negotiable and non-delegable. Look at your calendar
and pick a launch date where being glued to a screen for two straight days costs
you nothing else. Launching into a week you can't defend is just donating the
dev buy.

---

Nothing here is legal or financial advice, and I'm not a lawyer. If this ever
grows past a joke — real money, real holders — talk to someone qualified on SA
crypto asset rules before you take another step.
