#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Launcher script kept for backward compatibility.

In v0.1, ``python app/index.py`` was the only way to start the server, so this
is kept as a compatibility shim to avoid breaking existing systemd units and docs.

For new installations, the following is recommended::

    pip install dnsmasq-webconf
    dnsmasq-webconf --help

Note: since v0.2, the default listen address changed from 0.0.0.0 to 127.0.0.1.
To expose it on the LAN, specify ``--host 0.0.0.0 --auth USER:PASSWORD``.
"""

import os
import sys

# Make the package importable even when run straight from a cloned repository
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dnsmasq_webconf.app import main  # noqa: E402

if __name__ == '__main__':
    sys.stderr.write(
        '[Note] app/index.py is kept only for backward compatibility. '
        'Use the `dnsmasq-webconf` command instead.\n'
    )
    sys.exit(main())
