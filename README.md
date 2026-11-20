# gta6-trophy-rarity-forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/vnz347/gta6-trophy-rarity-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/vnz347/gta6-trophy-rarity-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts every GTA VI PlayStation 5 trophy's earn rate on Exophase at the
case's target, as five quantiles (5%, 25%, 50%, 75%, 95%) per trophy — all
55 of them (1 platinum, 2 gold, 6 silver, 46 bronze). A trophy's rate is a
ratio — earners over players tracked — and the two sides move for different
reasons in launch week, so the program forecasts them separately and
divides, instead of extrapolating the rate itself. Any per-trophy failure
falls back to a persistence forecast for that trophy, so a run always
writes a valid file.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

`main.py` (Python 3.13, standard library only, no network) reads every
`*.in` case in the input directory and writes one `<id>.out` forecast per
case to the output directory. Point the input directory at the kit's
`input/` (which holds the practice cases `p00.in` and `p01.in`) to try it
locally; the grader runs it as `python3 /bot/main.py` with `/input` and
`/output`. Each output is one JSON object, `{"trophies": [{"id", "q": [q05,
q25, q50, q75, q95]}]}`, one row per trophy, five non-decreasing quantiles
from 0 to 100. It runs in well under a second per case and is
deterministic: the same case always writes the same bytes.

## How the forecast works

**Players tracked.** Launch-week adoption is roughly a power law in time,
so log(players) is fitted against log(1 + hours since release) over the
last 12 hours of distinct reads, recent reads weighted more. The short
window tracks the current growth regime: a launch curve decelerates, so a
slope fitted over days overstates what comes next. The fitted slope is
damped for the extrapolation.

**Stale reads are collapsed first.** Exophase refreshes on its own
schedule, so long runs of reads show the same page — on launch night the
page sat unchanged for hours at 638 players tracked. Each run of identical
reads is one observation of that page state, timed at its last read;
keeping every read would let stale states dominate the fit.

**Earners per trophy.** log(earners + 1) is fitted the same way over the
last 24 hours of distinct reads, then shrunk toward a prior slope set by
the trophy's kind relative to the players slope: early story trophies'
earners grow with the player base, late story and grindy ones much slower.
The forecast is anchored at the last read's earner count.

**Structure.** Earners cannot fall, cannot exceed players tracked, follow
the story chain (a later chapter cannot have more earners than an earlier
one), and the platinum cannot have more earners than any other trophy.

**Spread.** Each trophy's quantiles are its median moved on the logit
scale by a spread combining the fit's prediction error, the players
forecast's uncertainty, a random-walk floor that grows with the horizon,
and extra width for trophies with only a handful of earners and for
histories with few distinct reads, where the trend is barely identified.

## Why this beats persistence

On launch night the tracked base was 638 players, nearly all of them
pre-release players; by the next morning it was tens of thousands of
ordinary players, and most trophies' rates had collapsed from dilution
even as their earner counts grew. A persistence forecast (the house
baseline) misses that entirely. Separating earners from players tracked
is what lets this forecast see dilution coming — and, later in the week,
see rates recover as progression outruns a slowing player base.

## Backtests

- **Real launch-night backtest:** history cut at 2026-11-19T03:00Z,
  scored against the live Exophase rates on 2026-11-20T07:24Z (31.4
  hours ahead, across the ~80x growth of the tracked base). Mean pinball
  loss 1.496, against the house baseline's 2.986.
- **Synthetic final-regime backtest:** a 118-read case over two days,
  4.5-day horizon, generated from saturating curves rather than the
  model's own family. Mean pinball loss 0.214, against the baseline's
  0.244.

---

<sub>Entered on [make.fun](https://make.fun/?ref=qa6gng8g&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
