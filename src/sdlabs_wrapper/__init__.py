import logging
import sys
from logging.config import dictConfig

import yaml

try:
    with open("config/logging.yml", mode="r") as config_file:
        dictConfig(yaml.safe_load(config_file))
except FileNotFoundError:
    logging.basicConfig(
        level=logging.NOTSET, handlers=[logging.StreamHandler(sys.stdout)]
    )
