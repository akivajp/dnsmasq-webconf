#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entry point for launching via ``python -m dnsmasq_webconf``."""

import sys

from .app import main

if __name__ == '__main__':
    sys.exit(main())
