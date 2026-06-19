"""Support ``python -m open_banking_pipeline`` as an alias for ``open-banking-ingest``."""

import sys

from open_banking_pipeline.cli import main

sys.exit(main())
