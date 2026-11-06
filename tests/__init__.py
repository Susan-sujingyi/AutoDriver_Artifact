"""Host experiment tools leave frozen dependency sources unchanged."""
import sys

# Direct unittest and host-tool entry points must not write __pycache__ beside
# imported third_party source. Isolated methods already use the same policy.
sys.dont_write_bytecode = True
