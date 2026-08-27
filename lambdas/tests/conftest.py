"""Test-wide AWS environment.

Several Lambda modules build boto3 clients at import time. Without a region and
dummy credentials in the environment, importing them raises before a single
assertion runs — and with real credentials in the environment, a mis-stubbed
test could reach a real account. Both are prevented here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_LAMBDAS = Path(__file__).resolve().parents[1]

# Each Lambda package is its own sys.path root at runtime, which is why the
# handlers import their siblings flat (`from parser import parse_workbook`).
# Reproduce that here so handler modules are importable in-process.
for _pkg in ("excel_parser", "excel_writer", "hard_rules", "confidence_scorer"):
    _p = str(_LAMBDAS / _pkg)
    if _p not in sys.path:
        sys.path.append(_p)

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ.pop("AWS_PROFILE", None)
