# Proposals for code-level readers

The agent may never write or run Python. When a job board can only be read with code (for example a
non-JSON API, signed requests, or a list that needs several dependent calls a reader spec cannot express),
the agent writes a proposal here as `<name>.md`: the board, the evidence URLs, the requests a reader would
make, and why a declarative spec in `schema/readers/` is not enough. A human reviews it and, if accepted,
implements the reader in `scripts/ats.py` with tests.
