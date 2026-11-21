#!/usr/bin/env python3
"""Differential test: guarded currency-token replacement must be byte-identical.

The baseline module is reconstructed by reversing ``patch.diff`` in memory: the
patched source is read, the guarded block is replaced by the three original
upstream statements, and the resulting module is exec'd.  Both implementations
then format the same inputs and every output is compared byte-for-byte.

Independent-sample accounting: every input is canonicalised to a key and
sha256-deduplicated *before* formatting, so the reported N is the number of
genuinely distinct inputs, not loop iterations.
"""
from __future__ import annotations

import hashlib
import json
import types
from decimal import Decimal

import babel.numbers as opt

NEW_BLOCK = """        if '¤' in retval and currency is not None:
            # ``str.replace`` evaluates its arguments eagerly, so calling
            # ``get_currency_name`` unconditionally pays for a plural-form
            # global-data lookup even when the pattern has no ``¤¤¤`` token.
            # Guard each replacement; the result is byte-identical.
            if '¤¤¤' in retval:
                retval = retval.replace('¤¤¤', get_currency_name(currency, value, locale))
            if '¤¤' in retval:
                retval = retval.replace('¤¤', currency.upper())
            if '¤' in retval:
                retval = retval.replace('¤', get_currency_symbol(currency, locale))
"""

OLD_BLOCK = """        if '¤' in retval and currency is not None:
            retval = retval.replace('¤¤¤', get_currency_name(currency, value, locale))
            retval = retval.replace('¤¤', currency.upper())
            retval = retval.replace('¤', get_currency_symbol(currency, locale))
"""


def load_baseline():
    with open(opt.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert src.count(NEW_BLOCK) == 1, "guarded block not found exactly once"
    baseline_src = src.replace(NEW_BLOCK, OLD_BLOCK, 1)
    assert OLD_BLOCK in baseline_src
    mod = types.ModuleType("babel_numbers_baseline")
    mod.__file__ = "<baseline:babel/numbers.py>"
    mod.__package__ = "babel"
    exec(compile(baseline_src, "<baseline numbers.py>", "exec"), mod.__dict__)
    # ``babel.core.Locale`` is shared between both modules and caches parsed
    # patterns as instances of the *optimized* ``NumberPattern`` class.  Bridge
    # the class-identity gap so the baseline parses them into its own class;
    # this shim is the only synthetic code in the reconstructed baseline.
    _baseline_parse = mod.parse_pattern

    def _compat_parse_pattern(pattern):
        if isinstance(pattern, opt.NumberPattern):
            pattern = pattern.pattern
        return _baseline_parse(pattern)

    mod.parse_pattern = _compat_parse_pattern
    return mod


BASELINE = load_baseline()

AMOUNTS = [
    "0", "-0", "1", "-1", "12.5", "1234.567", "-9876.54", "1234567.891",
    "0.0001", "1E+3", "1E-7", "99999999999999999999", "", "abc",
    "Infinity", "-Infinity", "NaN",
]
CURRENCIES = ["EUR", "USD", "JPY", "INR", "XAU", "ZZZ", "eur"]
FORMAT_TYPES = ["standard", "name", "accounting"]


def candidates():
    from babel import localedata

    locales = sorted(localedata.locale_identifiers())
    for i, locale_id in enumerate(locales):
        amount = AMOUNTS[i % len(AMOUNTS)]
        currency = CURRENCIES[i % len(CURRENCIES)]
        fmt = FORMAT_TYPES[i % len(FORMAT_TYPES)]
        yield (locale_id, currency, amount, fmt, True)
    # Boundary / semantic cross-product on a small, high-signal set.
    for locale_id in ["en", "de_DE", "fi_FI", "ar_EG", "zh_CN", "fr_FR"]:
        for amount in AMOUNTS:
            for currency in CURRENCIES:
                for fmt in FORMAT_TYPES:
                    yield (locale_id, currency, amount, fmt, True)
                    yield (locale_id, currency, amount, fmt, False)


def _key(args):
    locale_id, currency, amount, fmt, quant = args
    return json.dumps([locale_id, currency, amount, fmt, quant], ensure_ascii=False)


def _run(fn, args):
    locale_id, currency, amount, fmt, quant = args
    try:
        value = Decimal(amount)
    except Exception:
        value = amount
    try:
        out = fn(value, currency, locale=locale_id, format_type=fmt,
                 decimal_quantization=quant)
        return ("ok", out)
    except Exception as exc:  # noqa: BLE001 - exceptions are part of the contract
        return ("exc", type(exc).__name__, str(exc))


def run_diff():
    seen = {}
    for args in candidates():
        key = hashlib.sha256(_key(args).encode("utf-8")).hexdigest()
        if key not in seen:
            seen[key] = args
    mismatches = []
    for key, args in seen.items():
        base = _run(BASELINE.format_currency, args)
        got = _run(opt.format_currency, args)
        if base != got:
            mismatches.append((args, base, got))
    return len(seen), mismatches


def test_byte_identical():
    n, mismatches = run_diff()
    assert n >= 1000, f"only {n} independent samples"
    assert not mismatches, f"{len(mismatches)} mismatches, first: {mismatches[0]}"


def test_no_eager_currency_name_lookup(monkeypatch):
    calls = []
    real = opt.get_currency_name

    def counting(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(opt, "get_currency_name", counting)
    # ``de_DE`` symbol pattern is ``#,##0.00 ¤``: no ``¤¤¤`` token, so the
    # plural-form currency-name lookup must not run at all.
    opt.format_currency(Decimal("1234.5"), "EUR", locale="de_DE")
    assert calls == []
    # ``format_type="name"`` uses ``¤¤¤`` and must still resolve the name.
    out = opt.format_currency(Decimal("1234.5"), "EUR", locale="fi",
                              format_type="name")
    assert out == "1\xa0234,50 euroa"
    assert len(calls) == 1


if __name__ == "__main__":
    n, mism = run_diff()
    print(f"independent samples: {n} (sha256-deduped)")
    print(f"mismatches: {len(mism)}")
    raise SystemExit(1 if mism else 0)
