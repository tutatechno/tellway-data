#!/usr/bin/env python3
"""A kivonat dátuma a HTTP `Last-Modified` fejlécből, `ÉÉÉÉ-HH-NN` alakban.

Ha nincs fejléc vagy értelmezhetetlen, a mai dátum. A csomag fejlécébe
és a katalógusba ez kerül, ebből látja az app, hogy van-e újabb változat.
"""
import email.utils
import sys
import time

raw = (sys.argv[1] if len(sys.argv) > 1 else '').split(':', 1)
value = raw[1].strip() if len(raw) == 2 else ''
try:
    print(email.utils.parsedate_to_datetime(value).strftime('%Y-%m-%d'))
except Exception:  # noqa: BLE001
    print(time.strftime('%Y-%m-%d'))
