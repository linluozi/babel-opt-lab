# babel-opt-lab — `format_currency` no longer pays for a plural-form currency-name lookup it discards

[Babel](https://github.com/python-babel/babel) is the standard CLDR-backed
internationalization library for Python. Its `NumberPattern.apply()` computes a
currency name for the `¤¤¤` (long-name) token on **every** call that contains
any currency token, then throws the result away when the pattern only has `¤`
or `¤¤`. This lab guards the three `str.replace` calls on their actual token
presence: **2.04x median speedup** on the mixed-locale workload, **2.03x on all
1147 CLDR locales**, with **5,426 sha256-deduped differential samples and 0
byte differences**.

Upstream: https://github.com/python-babel/babel

Base commit: `aeca9fa259ad50e790b51a8475bfa415888932ee` (babel 2.18.0)

## What changed

```python
# before: str.replace() evaluates its argument eagerly, so get_currency_name()
# runs even when the pattern has no ¤¤¤ token, and its result is discarded.
if '¤' in retval and currency is not None:
    retval = retval.replace('¤¤¤', get_currency_name(currency, value, locale))
    retval = retval.replace('¤¤', currency.upper())
    retval = retval.replace('¤', get_currency_symbol(currency, locale))
```

```python
# after: each replacement is guarded by the token it replaces; byte-identical.
if '¤' in retval and currency is not None:
    if '¤¤¤' in retval:
        retval = retval.replace('¤¤¤', get_currency_name(currency, value, locale))
    if '¤¤' in retval:
        retval = retval.replace('¤¤', currency.upper())
    if '¤' in retval:
        retval = retval.replace('¤', get_currency_symbol(currency, locale))
```

`git diff --stat`: **1 file changed, 10 insertions(+), 3 deletions(-)** (13 lines,
well under the 80-line cap).

## Results

Locale objects (the form upstream's own `tests/benchmarks/benchmark_numbers.py`
uses); 3 warmup + 5 timed samples per scale; median reported.

| scale | calls/sample | baseline median | optimized median | speedup | p25 / p75 (opt) | rel. stdev |
|---|---:|---:|---:|---:|---:|---:|
| small | 2,000 | 18.369 µs | 9.982 µs | **1.84x** | 9.141 / 11.319 µs | 20.6% |
| medium | 20,000 | 18.386 µs | 9.024 µs | **2.04x** | 8.799 / 9.396 µs | 3.8% |
| large | 100,000 | 18.164 µs | 8.741 µs | **2.08x** | 8.654 / 8.748 µs | 0.6% |
| **real corpus** (1147 CLDR locales) | 114,700 | 21.321 µs | 10.497 µs | **2.03x** | 10.085 / 10.499 µs | 3.7% |

G3 median of small/medium/large = **2.038x** (gate ≥1.15x).
G3b real corpus = **2.031x** (gate ≥1.15x).
G4 large scale = **2.078x** (gate ≥0.95x).

The `small` row's 20.6% relative stdev is the one noisy cell: 2,000 calls run in
~20 ms, so scheduler wakeups and the first-touch page faults dominate. It is
reported rather than hidden; medium/large/real are all ≤3.8%.

**Honesty note on API form.** The gain above passes a pre-parsed `Locale` object.
If callers pass a locale *string*, `format_currency` must run `Locale.parse()`
on every call, which is ~6 µs of unrelated work and dilutes the speedup:
the same medium workload measures 25.004 µs → 14.124 µs = **1.77x**
(`benchmark/raw-string/`). Both numbers are real; neither is used to stand in
for the other.

## Differential test (G2)

`tests/test_diff.py` reconstructs the unpatched baseline by replacing the
guarded block in `babel/numbers.py` with the original three statements, execs
it, and compares every output **byte-for-byte** (`==` on the resulting string,
or `(exception type, message)` when the input raises).

```
$ python tests/test_diff.py
independent samples: 5426 (sha256-deduped)
mismatches: 0
```

Inputs cover all 1147 CLDR locales, empty string / `Infinity` / `NaN` / `1E+3`
boundary amounts, unknown and lowercase currency codes, all three
`format_type` values (`standard`, `name`, `accounting`), and both
`decimal_quantization` settings. Every input is canonicalised and
**sha256-deduplicated before formatting**, so 5,426 is the number of genuinely
distinct inputs, not loop iterations. One test in the same file also asserts
that `de_DE`'s symbol pattern (`#,##0.00 ¤`) never calls
`get_currency_name()`, while `format_type="name"` still resolves `euroa`.

## Gates

| gate | result | reproduce |
|---|---|---|
| G1 upstream tests | **8054 passed, 2 skipped, 2 xfailed, 0 failed** (13.44 s) | `python -m pytest tests -q -p no:cacheprovider` |
| G2 diff ≥1000 | **5,426 independent, 0 mismatches** | `python tests/test_diff.py` |
| G3 median speedup | **2.038x** (≥1.15x) | `python benchmark/bench.py --impl {baseline,optimized} --out ...` |
| G3b real corpus | **2.031x** (≥1.15x) | `--scale real` (1147 CLDR locales) |
| G4 large scale | **2.078x** (≥0.95x) | `--scale large` |
| G5 peak RSS | **18,312 KB vs 18,432–19,568 KB baseline** (0.99x, ≤1.10x) | `/usr/bin/time -f %M python benchmark/mem_probe.py` |

## Reproduce

```bash
python -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install pytest
# CLDR data is git-ignored upstream; fetch it once (needed to run the suite):
.venv/bin/python scripts/download_import_cldr.py
python -m pytest tests -q -p no:cacheprovider && python tests/test_diff.py
```

Benchmark both sides (baseline = patch reverted):

```bash
python benchmark/bench.py --impl optimized --out benchmark/optimized.json
git apply -R patch.diff
python benchmark/bench.py --impl baseline --out benchmark/baseline.json
git apply patch.diff   # restore
```

## 本轮创新

**B 档 — 首次把 Babel `format_currency` 的「被丢弃的复数货币名查询」量化为
可归因开销并给出零语义差异的守卫改写。** 台账里没有针对 Babel 货币路径的条目
（`speedgap` 是合成 vs 真实负载对账，`inflect` 是 typeguard 内联）。新结论：
符号格式（`¤` 单 token）每次调用都无条件执行 `get_currency_name()` 的 CLDR
全局数据 + 复数规则查询，实测占 `format_currency` 总耗时 **~50%**；守卫后
2.03x，且在 5,426 个独立真实 locale 输入上逐字节一致。与「换上游重跑」不同，
本结论可被 `tests/test_diff.py::test_no_eager_currency_name_lookup` 直接证伪——
该测试断言 `de_DE` 符号路径对 `get_currency_name` 的调用次数为 0。

## Environment

- CPU: AMD EPYC 7K62 48-Core Processor (4 vCPU), x86_64
- RAM: 3.7 GB
- Python: 3.12.3 (`/usr/bin/python3.12`); Node.js not used
- Babel: 2.18.0 @ `aeca9fa259ad50e790b51a8475bfa415888932ee`
- Seed: `20261121`; warmup 3, samples 5; timing `time.perf_counter()`
- Raw samples: `benchmark/raw/*.json` (object locale) and
  `benchmark/raw-string/*.json` (string locale) — every sample on disk

## License

BSD-3-Clause, retaining the Babel Team's original copyright. See `LICENSE`.
