# -*- coding: utf-8 -*-
import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt5.QtWidgets")


def test_dialog_smoke():
    script = os.path.join(os.path.dirname(__file__), "smoke", "run_smoke.py")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SMOKE OK" in proc.stdout
