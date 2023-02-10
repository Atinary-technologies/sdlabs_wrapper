import pathlib
from typing import Dict

from sdlabs_wrapper.models import OptimizationConfig

DEFAULT_CONFIG_PATH = str(pathlib.Path("config/optimization_config.json").resolve())
CFG = None


def init(
    config_path: str = None, config_dict: Dict[str, any] = None, api_key: str = None
) -> OptimizationConfig:
    global CFG
    if not CFG:
        if not config_dict:
            config_path = config_path or DEFAULT_CONFIG_PATH
        else:
            config_path = None
        CFG = OptimizationConfig(
            spec_file_path=config_path, input_content=config_dict, api_key=api_key
        )
    return CFG
