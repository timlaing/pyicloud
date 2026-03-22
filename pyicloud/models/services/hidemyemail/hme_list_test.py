"""
Demo script to load a JSON file containing a Hide My Email "list" endpoint response
and demonstrate datetime parsing for timestamps and create_timestamps.

Usage:
    python load_list_response_demo.py path/to/list_response.json
"""

import argparse
import json

from rich import pretty
from rich.console import Console
from rich.traceback import install

# Import the Pydantic model (ensure your models are on PYTHONPATH or adjust import)
from pyicloud.models.services.hidemyemail.hidemyemail_models import (
    HideMyEmailListResponse,
)

install(show_locals=True)
pretty.install()

console = Console()


def main():
    """
    Demo script to load a JSON file containing a Hide My Email "list" endpoint response
    """
    parser = argparse.ArgumentParser(
        description="Load and validate a Hide My Email list response, printing datetime fields."
    )
    parser.add_argument(
        "json_path",
        help="Path to the JSON file with the 'list' endpoint response",
    )
    args = parser.parse_args()

    # Load raw JSON
    with open(args.json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Validate and parse into Pydantic model
    response = HideMyEmailListResponse.model_validate(data)

    console.rule("Response")
    console.print(response)


if __name__ == "__main__":
    main()
