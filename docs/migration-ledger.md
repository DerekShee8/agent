# Migration ledger

All imported files below were copied without transformation from the pinned Git object.

| Target | Source commit | Source path | Decision |
| --- | --- | --- | --- |
| `src/enterprise_rag/models.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `src/enterprise_rag/models.py` | Import, then extend in Task 3 |
| `src/enterprise_rag/ports.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `src/enterprise_rag/ports.py` | Import unchanged |
| `src/enterprise_rag/errors.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `src/enterprise_rag/errors.py` | Import unchanged |
| `src/enterprise_rag/ingestion.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `src/enterprise_rag/ingestion.py` | Import unchanged |
| `tests/unit/test_models.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/unit/test_models.py` | Import unchanged as audited coverage |
| `tests/unit/test_ports.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/unit/test_ports.py` | Import unchanged as audited coverage |
| `tests/unit/test_errors.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/unit/test_errors.py` | Import unchanged as audited coverage |
| `tests/unit/test_ingestion.py` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/unit/test_ingestion.py` | Import unchanged as audited coverage |
| `tests/fixtures/docs/alpha.md` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/fixtures/docs/alpha.md` | Import unchanged as audited fixture |
| `tests/fixtures/docs/beta.txt` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/fixtures/docs/beta.txt` | Import unchanged as audited fixture |
| `tests/fixtures/docs/ignored.pdf` | `f09229248a354d652ac04ef249be0abb1549fbcd` | `tests/fixtures/docs/ignored.pdf` | Import unchanged as textual fake PDF fixture |

## Explicit exclusions

The following source material was **NOT imported**: `src/enterprise_rag/config.py`, source `pyproject.toml`, source `uv.lock`, `.env.example`, old plans, learning files, caches, and uncommitted handoffs.
