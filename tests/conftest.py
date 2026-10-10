import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# All tests use a throwaway database. This must be set before `app` is imported.
os.environ["FOOD_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import config  # noqa: E402

# The app tests need a trained model; train one if it does not exist yet.
if not os.path.exists(config.MODEL_PATH):
    subprocess.run([sys.executable, "train_model.py"], cwd=ROOT, check=True)

import pytest  # noqa: E402


@pytest.fixture()
def appmod():
    import app as module
    module.reset_demo()
    return module


@pytest.fixture()
def client(appmod):
    return appmod.app.test_client()
