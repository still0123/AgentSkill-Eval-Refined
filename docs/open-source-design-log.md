# Open-Source Design Log

Record external design research before code adoption. Local bug fixes derived only from this
repository are marked `local-only` and do not invent an external attribution.

| Date | Repository | License | Pattern reviewed | Adopted | Rejected / reason | Project adaptation | Verification |
|---|---|---|---|---|---|---|---|
| 2026-08-06 | local-only | N/A | Preserve infrastructure invalidity outside task W/T/L | Explicit final `INVALID` classification and decision | Treating invalid as failed task evidence; semantically wrong | Backward-compatible `invalid_count=0`; confirmation fails closed | 148 tests, Ruff, mypy, wheel clean-room Demo verification |

Future entries must include an exact repository URL and revision when external code or design
material affects implementation.
