#!/usr/bin/env python3
"""Peak-RSS probe for gate G5.

Formats 200,000 currencies with a pre-parsed Locale, then prints the process
peak RSS (``ru_maxrss`` is KiB on Linux).  Run once with ``patch.diff`` applied
and once reverted; compare the reported values at <=1.10x.
"""
from __future__ import annotations

import resource
from decimal import Decimal

from babel.core import Locale
from babel.numbers import format_currency

LOCALE = Locale.parse("de_DE")
VALUE = Decimal("1234.5")

for _ in range(200_000):
    format_currency(VALUE, "EUR", locale=LOCALE)

print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
