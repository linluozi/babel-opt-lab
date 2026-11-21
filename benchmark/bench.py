#!/usr/bin/env python3
"""Benchmark Babel's ``format_currency`` hot path.

Measures whichever ``babel`` package is importable (run twice: once with
``patch.diff`` applied, once with it reverted -- see README).  Every sample is
written to ``benchmark/raw/<impl>-<scale>-<i>.json`` so the summary can be
recomputed from raw data instead of trusted.

Scales
------
small / medium / large
    20 real CLDR locales x 6 real amounts, cycled to the requested number of
    ``format_currency`` calls.  This is the mixed workload.
real
    Every locale shipped in Babel's CLDR data (1147), each formatted in its own
    territory currency, repeated ``REAL_REPEATS`` times.  This is the real
    corpus used for gate G3b.

Timing uses ``time.perf_counter()``; warmup iterations are excluded from the
samples.  Seed is fixed for reproducibility.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import babel
from babel import localedata
from babel.core import Locale
from babel.numbers import format_currency, get_territory_currencies

SEED = 20261121
WARMUP = 3
SAMPLES = 5
REAL_REPEATS = 100

MIXED_LOCALES = [
    "en_US", "fi_FI", "de_DE", "fr_FR", "ja_JP", "ru_RU", "ar_EG", "zh_CN",
    "pt_BR", "es_ES", "it_IT", "nl_NL", "pl_PL", "sv_SE", "tr_TR", "ko_KR",
    "hi_IN", "th_TH", "cs_CZ", "da_DK",
]
MIXED_AMOUNTS = ["0", "1", "12.5", "1234.567", "-9876.54", "1234567.891"]
SCALE_SIZES = {"small": 2_000, "medium": 20_000, "large": 100_000}


def _currency_for(locale_id: str) -> str:
    territory = locale_id.split("_")[-1] if "_" in locale_id else locale_id
    try:
        currencies = get_territory_currencies(territory)
    except Exception:
        currencies = ()
    return currencies[0] if currencies else "USD"


def mixed_ops(n: int) -> list[tuple[Locale, str, Decimal]]:
    """n calls cycling through 20 locales x 6 amounts (120 unique pairs)."""
    parsed = [Locale.parse(x) for x in MIXED_LOCALES]
    ops = []
    for i in range(n):
        locale = parsed[i % len(parsed)]
        amount = MIXED_AMOUNTS[(i // len(parsed)) % len(MIXED_AMOUNTS)]
        ops.append((locale, "EUR", Decimal(amount)))
    return ops


def real_corpus() -> list[tuple[Locale, str, Decimal]]:
    """One call per CLDR locale, in that locale's own territory currency."""
    ops = []
    for locale_id in sorted(localedata.locale_identifiers()):
        try:
            locale = Locale.parse(locale_id)
        except Exception:
            continue
        ops.append((locale, _currency_for(locale_id), Decimal("1234.5")))
    base = ops
    ops = list(base)
    for _ in range(REAL_REPEATS - 1):
        ops.extend(base)
    return ops


def build(scale: str, locale_mode: str = "object") -> list[tuple[object, str, Decimal]]:
    if scale == "real":
        ops = real_corpus()
    else:
        ops = mixed_ops(SCALE_SIZES[scale])
    if locale_mode == "string":
        ops = [(str(loc), c, v) for loc, c, v in ops]
    return ops


def run_sample(ops) -> float:
    start = time.perf_counter()
    for locale, currency, value in ops:
        format_currency(value, currency, locale=locale)
    return time.perf_counter() - start


def env_info(locale_mode: str = "object") -> dict:
    try:
        cpu = platform.processor() or os.uname().machine
    except Exception:
        cpu = "unknown"
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu": cpu,
        "babel": babel.__version__,
        "babel_file": babel.__file__,
        "seed": SEED,
        "warmup": WARMUP,
        "samples": SAMPLES,
        "locale_mode": locale_mode,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", required=True, choices=["baseline", "optimized"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-dir", default=os.path.join(os.path.dirname(__file__), "raw"))
    ap.add_argument("--scales", default="small,medium,large,real")
    ap.add_argument("--locale-mode", default="object", choices=["object", "string"])
    args = ap.parse_args()
    os.makedirs(args.raw_dir, exist_ok=True)

    summary = {"impl": args.impl, "env": env_info(args.locale_mode), "scales": {}}
    for scale in args.scales.split(","):
        ops = build(scale, args.locale_mode)
        for _ in range(WARMUP):
            run_sample(ops)
        times = []
        for i in range(SAMPLES):
            seconds = run_sample(ops)
            times.append(seconds)
            raw = {
                "impl": args.impl, "scale": scale, "sample_index": i,
                "ops": len(ops), "seconds": seconds,
                "ns_per_op": seconds / len(ops) * 1e9,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "python": summary["env"]["python"], "seed": SEED,
            }
            path = os.path.join(args.raw_dir, f"{args.impl}-{scale}-{i}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
        ns = [t / len(ops) * 1e9 for t in times]
        med = statistics.median(ns)
        p25, p75 = statistics.quantiles(ns, n=4, method="inclusive")[0], statistics.quantiles(ns, n=4, method="inclusive")[2]
        summary["scales"][scale] = {
            "ops_per_sample": len(ops),
            "samples_seconds": times,
            "ns_per_op_median": med,
            "ns_per_op_p25": p25,
            "ns_per_op_p75": p75,
            "rel_stdev_pct": (statistics.pstdev(ns) / med * 100) if med else 0.0,
        }
        print(f"{args.impl:9s} {scale:7s} ops={len(ops):7d} "
              f"median={med/1000:9.3f} us  p25={p25/1000:9.3f}  p75={p75/1000:9.3f}  "
              f"rsd={summary['scales'][scale]['rel_stdev_pct']:.2f}%")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
