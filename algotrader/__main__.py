"""Allow `python -m algotrader` to invoke the CLI."""
from algotrader.cli import main
import sys

sys.exit(main())
