import os
from unittest.mock import patch

import pytest


class FileSystemAccessError(Exception):
    pass


@pytest.fixture(autouse=True, scope="function")
def mock_mkdir():
    mkdir = os.mkdir

    def my_mkdir(path, *args, **kwargs):
        if "python-test-results" not in path:
            raise FileSystemAccessError(
                f"You should not be creating directories in tests. {path}"
            )
        return mkdir(path, *args, **kwargs)

    with patch("os.mkdir", my_mkdir) as mock_mkdir:
        yield mock_mkdir


@pytest.fixture(autouse=True, scope="session")
def mock_open_fixture():
    builtins_open = open

    def my_open(path, *args, **kwargs):
        if "python-test-results" not in path:
            raise FileSystemAccessError(
                f"You should not be opening files in tests. {path}"
            )
        return builtins_open(path, *args, **kwargs)

    with patch("builtins.open", my_open) as mock_open:
        yield mock_open
