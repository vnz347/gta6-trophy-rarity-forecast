"""Forecast GTA VI trophy earn rates on Exophase at a case's target.

Model: a trophy's earn rate is a ratio, earners / players tracked, and the two
sides move for different reasons, so this program forecasts them separately
and divides, instead of extrapolating the rate itself.

  - Players tracked.  Launch-week adoption is roughly a power law in time, so
    log(players) is fitted against log(1 + hours since release) over the
    last 12 hours of distinct reads (consecutive identical reads are one
    stale page, not fresh evidence, and are collapsed first), with recent
    reads weighted more, and read at the target.
  - Earners per trophy.  log(earners + 1) is fitted the same way over the
    last 24 hours of distinct reads, then shrunk toward a prior slope set by
    the trophy's kind relative to the players slope (early story trophies'
    earners grow with the player base, late and grindy ones much slower),
    and anchored at the last read's earner count.  Both slopes are damped
    for the extrapolation, since launch growth decelerates.
  - Structure.  Earners cannot fall, cannot exceed players tracked, follow
    the story chain (chapter n+1 cannot have more earners than chapter n),
    and the platinum cannot have more earners than any other trophy.
  - Spread.  Each trophy's quantiles are its median moved on the logit scale
    by a spread that combines the fit's prediction error, the players
    forecast's uncertainty, a random-walk floor that grows with the horizon,
    and extra width for trophies with only a handful of earners and for
    histories with few distinct reads, where the trend is barely identified.

Runs on Python 3.13, standard library only, no network, deterministic: the
same case always writes the same bytes.  Any per-trophy failure falls back to
a persistence forecast for that trophy, so a run always writes a valid file.
"""

import json
import math
import os
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
NORM = statistics.NormalDist()
RELEASE = datetime(2026, 11, 19, tzinfo=timezone.utc)

# Prior earners-growth exponents, RELATIVE to the players exponent: early
# trophies' earners grow with (or faster than) the player base, late and
# grindy trophies' much slower.  A trophy's prior slope is the players slope
# plus its delta, so the forecast stays coherent across growth regimes.
PRIOR_DELTA = {
    "prologue": 0.0,
    "story_early": 0.5,
    "story_mid": -0.7,
    "story_late": -1.0,
    "side": -0.6,
    "misc": 0.0,
    "grind": -1.0,
    "completion": -1.5,
    "platinum": -1.5,
}
# How much the prior counts, in pseudo-observations, against a fitted slope.
PRIOR_WEIGHT = 2.0
# Players exponent to fall back on when the history shows no growth (a flat
# launch-night page): the tracked base exploded roughly like (1 + hours)^2
# in the first day, from 638 players to tens of thousands.
PLAYERS_PRIOR_SLOPE = 2.0
PLAYERS_PRIOR_WEIGHT = 3.0
# Random-walk floor for the spread, in logit units per square-root day.
# Calibrated on backtests: 0.30-0.38 beats the persistence baseline on both
# a real launch-night backtest and a synthetic final-regime case, while the
# trend and players terms still carry most of the width; 0.35 keeps a
# margin for the refresh jumps the synthetic world does not have.
WALK_SIGMA = 0.35


def clamp_rate(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def logistic(x):
    if x >= 0:
        return 100.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return 100.0 * e / (1.0 + e)


def round4(x):
    """Round to 4 decimals, half up, returning an int for whole numbers."""
    scaled = x * 10000.0
    whole = math.floor(scaled + 0.5)
    value = whole / 10000.0
    return int(value) if value == int(value) else value


def parse_time(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def hours_since_release(moment):
    return max(0.0, (moment - RELEASE).total_seconds() / 3600.0)


def fit_line(xs, ys, weights=None):
    """Least-squares line; returns (slope, intercept, resid_std, n)."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0, 0.0, 0
    if weights is None:
        weights = [1.0] * n
    sw = sum(weights)
    mx = sum(w * x for w, x in zip(weights, xs)) / sw
    my = sum(w * y for w, y in zip(weights, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(weights, xs))
    if sxx <= 0:
        resid = math.sqrt(sum(w * (y - my) ** 2 for w, y in zip(weights, ys)) / sw)
        return 0.0, my, resid, n
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(weights, xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    resid = math.sqrt(sum(w * (y - (intercept + slope * x)) ** 2
                          for w, x, y in zip(weights, xs, ys)) / sw)
    return slope, intercept, resid, n


def dedupe_reads(history):
    """Collapse consecutive reads of an unchanged page into their last read.

    Exophase refreshes on its own schedule, so long runs of reads show the
    same page.  Each run is one observation of that page state, timed at its
    last read; keeping every read would let stale states dominate the fit.
    """
    kept = []
    for read in history:
        key = (read.get("players_tracked"),
               tuple(read.get("rates") or []))
        if kept and kept[-1][0] == key:
            kept[-1] = (key, read)
        else:
            kept.append((key, read))
    return [read for _, read in kept]


def classify(trophy):
    """Kind of trophy, from its tier, name and description."""
    if trophy.get("tier") == "platinum":
        return "platinum"
    text = (trophy.get("name", "") + " " + trophy.get("description", "")).lower()
    if "100%" in text and "completion" in text:
        return "completion"
    match = re.search(r"chapter (\d+)", text)
    if match:
        chapter = int(match.group(1))
        return "story_early" if chapter <= 2 else "story_mid"
    if "final mission" in text and "complete the final mission" in text:
        return "story_late"
    if "final mission for" in text:
        return "side"
    grind_words = ("250 stunts", "100 locations", "every spot", "50 challenges",
                   "20 different races", "every location", "3 collectible",
                   "gold medal in all", "8 mission strands")
    if any(word in text for word in grind_words):
        return "grind"
    return "misc"


def chapter_index(trophy):
    """Position in the story chain, or None when the trophy is not in it."""
    text = (trophy.get("name", "") + " " + trophy.get("description", "")).lower()
    match = re.search(r"chapter (\d+)", text)
    if match:
        return int(match.group(1))
    if "complete the final mission" in text and trophy.get("tier") == "gold":
        return 99
    return None


def forecast_players(reads, target_hours, checkpoint_hours):
    """Players tracked at the target, with slope, spread and decay.

    Fits log(players) against log(1 + hours since release) over the last 12
    hours of distinct reads, recent reads weighted more, and anchors the
    forecast at the last read's count.  The short window tracks the current
    growth regime: a launch curve decelerates, so a slope fitted over days
    overstates what comes next.  With too little signal the slope falls
    back on the launch prior.  The slope is damped for the extrapolation.
    Returns (players_target, slope_undamped, spread, last_hours, decay).
    """
    window = [r for r in reads
              if hours_since_release(parse_time(r["at"])) >= checkpoint_hours - 12.0]
    if len(window) < 2:
        window = reads
    xs, ys, ws = [], [], []
    for i, read in enumerate(window):
        players = read.get("players_tracked")
        if not players or players <= 0:
            continue
        xs.append(math.log1p(hours_since_release(parse_time(read["at"]))))
        ys.append(math.log(players))
        ws.append(1.0 + 2.0 * i / max(1, len(window) - 1))
    last_hours = hours_since_release(parse_time(reads[-1]["at"]))
    last_players = reads[-1].get("players_tracked") or 1
    n = len(xs)
    if n == 0:
        return None, PLAYERS_PRIOR_SLOPE, 1.2, last_hours, 1.0
    slope, _, resid, _ = fit_line(xs, ys, ws)
    if n < 2 or len(set(xs)) < 2:
        slope = PLAYERS_PRIOR_SLOPE
    else:
        slope = (n * slope + PLAYERS_PRIOR_WEIGHT * PLAYERS_PRIOR_SLOPE) / (
            n + PLAYERS_PRIOR_WEIGHT)
    slope = min(max(slope, 0.0), 3.0)
    # Damp the fitted slope for the extrapolation, since launch growth
    # decelerates; but a slope that is mostly prior (too few distinct reads
    # to fit) is already a whole-window average, so it is not damped.
    decay = 1.0 if n < 3 else ((1.0 + last_hours) / (1.0 + target_hours)) ** 0.25
    damped = slope * decay
    players = last_players * ((1.0 + target_hours) / (1.0 + last_hours)) ** damped
    # Never below the last read.  The cap is generous on purpose: the tracked
    # base grew ~80x in the first 31 hours, so a tight cap would break the
    # forecast exactly when growth is fastest; it only rules out absurdity.
    players = min(max(players, last_players), 5_000_000, last_players * 200.0)
    spread = min(1.2, max(0.2, resid * 1.5 + 0.15 + 1.0 / math.sqrt(n)))
    return players, slope, spread, last_hours, decay


def forecast_earners(reads, index, kind, target_hours, checkpoint_hours,
                     players_slope, decay, n_distinct):
    """Earners of one trophy at the target, with a log-scale uncertainty."""
    points = []
    for read in reads:
        rates = read.get("rates") or []
        if index >= len(rates) or rates[index] is None:
            continue
        players = read.get("players_tracked")
        if not players or players <= 0:
            continue
        at = parse_time(read["at"])
        hours = hours_since_release(at)
        earners = rates[index] / 100.0 * players
        points.append((hours, earners))
    if not points:
        return 0.0, 1.5, 0.0
    last_hours, last_earners = points[-1]
    recent = [(h, e) for h, e in points if h >= checkpoint_hours - 24.0]
    if len(recent) < 2:
        recent = points
    xs = [math.log1p(h) for h, _ in recent]
    ys = [math.log(e + 1.0) for _, e in recent]
    prior = max(0.0, players_slope + PRIOR_DELTA.get(kind, 0.0))
    if len(set(xs)) < 2:
        # One distinct observation says nothing about the slope: use the prior.
        slope, resid, n = prior, 0.5, 0
    else:
        fitted, _, resid, n = fit_line(xs, ys)
        slope = (n * fitted + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT)
    slope = min(max(slope, 0.0), 2.5) * decay
    growth = ((1.0 + target_hours) / (1.0 + last_hours)) ** slope
    earners = max(last_earners, 0.5) * growth
    base = max(0.25, resid * 2.0 + 0.25)
    spread = math.sqrt(base ** 2 + (1.5 / math.sqrt(max(1, n_distinct))) ** 2)
    spread = min(2.0, spread)
    if last_earners < 5:
        spread = max(spread, 0.7)
    elif last_earners < 20:
        spread = max(spread, 0.4)
    return earners, spread, last_earners


def persistence_row(last_rate, days, levels):
    """Fallback forecast: the last rate, with a random-walk spread."""
    spread = 0.5 * math.sqrt(max(days, 0.0))
    centre = logit(last_rate if last_rate is not None else 1.0)
    return [round4(min(100.0, max(0.0, logistic(centre + NORM.inv_cdf(t) * spread))))
            for t in levels]


def forecast_case(case, name):
    levels = case.get("levels") or LEVELS
    days = case["horizon_minutes"] / 1440.0
    checkpoint = parse_time(case["at"])
    target = parse_time(case["target"])
    checkpoint_hours = hours_since_release(checkpoint)
    target_hours = hours_since_release(target)
    trophies = case["trophies"]
    reads = dedupe_reads(case.get("history") or [])
    if not reads:
        return {"trophies": [{"id": t["id"], "q": persistence_row(None, days, levels)}
                             for t in trophies]}
    last = reads[-1]
    last_rates = last.get("rates") or []
    n_distinct = len(reads)
    players_target, players_slope, players_spread, _, decay = forecast_players(
        reads, target_hours, checkpoint_hours)
    if players_target is None:
        players_target = last.get("players_tracked") or 1
        players_spread = 1.2
        players_slope = PLAYERS_PRIOR_SLOPE
        decay = 1.0

    kinds = [classify(t) for t in trophies]
    earners_target = []
    spreads = []
    for i, trophy in enumerate(trophies):
        try:
            earners, spread, _ = forecast_earners(
                reads, i, kinds[i], target_hours, checkpoint_hours,
                players_slope, decay, n_distinct)
        except Exception:
            earners, spread = 0.0, 1.5
        earners_target.append(earners)
        spreads.append(spread)

    # Structure: earners never exceed the players, follow the story chain,
    # and the platinum never exceeds any other trophy.
    earners_target = [min(e, players_target) for e in earners_target]
    chain = sorted((chapter_index(t), i) for i, t in enumerate(trophies)
                   if chapter_index(t) is not None)
    running = None
    for _, i in chain:
        if running is None:
            running = earners_target[i]
        else:
            running = min(running, earners_target[i])
            earners_target[i] = running
    platinum = [i for i, t in enumerate(trophies) if t.get("tier") == "platinum"]
    if platinum and earners_target:
        cap = min(earners_target)
        for i in platinum:
            earners_target[i] = min(earners_target[i], cap)

    rows = []
    walk = WALK_SIGMA * math.sqrt(max(days, 0.0))
    for i, trophy in enumerate(trophies):
        last_rate = last_rates[i] if i < len(last_rates) else None
        try:
            if last_rate is not None and last_rate >= 99.99:
                rows.append({"id": trophy["id"],
                             "q": [99.95, 99.99, 100, 100, 100]})
                continue
            median = 100.0 * earners_target[i] / players_target
            median = clamp_rate(median)
            total = math.sqrt(walk ** 2 + spreads[i] ** 2 + players_spread ** 2)
            total = min(2.5, max(0.15, total))
            centre = logit(median)
            quantiles = []
            for tau in levels:
                q = logistic(centre + NORM.inv_cdf(tau) * total)
                quantiles.append(round4(min(100.0, max(0.0, q))))
            # Rounding must not break the ordering.
            for j in range(1, len(quantiles)):
                if quantiles[j] < quantiles[j - 1]:
                    quantiles[j] = quantiles[j - 1]
            rows.append({"id": trophy["id"], "q": quantiles})
        except Exception:
            rows.append({"id": trophy["id"],
                         "q": persistence_row(last_rate, days, levels)})
    return {"trophies": rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / f"{path.stem}.out").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
