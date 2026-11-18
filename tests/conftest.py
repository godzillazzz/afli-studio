"""Test setup: one throwaway DATA_DIR for the whole session, set before app imports."""
import os
import tempfile

_DATA = tempfile.mkdtemp(prefix="afli-tests-")
os.environ["DATA_DIR"] = _DATA
os.environ["APP_PASSWORD"] = "testpw"
os.environ.setdefault("SUB_FONT", "Garuda")
