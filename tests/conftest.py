import pytest

from competence import seed
from competence.core.db import connect


@pytest.fixture
def db_path(tmp_path):
    """A fresh database with the full catalogue and no people or rounds."""
    path = tmp_path / "matrix.sqlite"
    seed.create_database(path)
    return path


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    yield c
    c.close()
