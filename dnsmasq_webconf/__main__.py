#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``python -m dnsmasq_webconf`` で起動するためのエントリポイント。"""

import sys

from .app import main

if __name__ == '__main__':
    sys.exit(main())
