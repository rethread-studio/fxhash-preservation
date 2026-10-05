"""swhprep: prepare plain source folders for archival by Software Heritage.

Pipeline: `convert` (folders -> bare git repos), `manifest` (grokmirror manifest.js.gz
for the SWH grokmirror lister), `check` (git ls-remote every derived origin URL).
"""

from __future__ import annotations

__version__ = "0.1.0"
