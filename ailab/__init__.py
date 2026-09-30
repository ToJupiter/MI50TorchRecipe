from . import bootstrap

bootstrap.configure()

from .config import DTYPE_NAMES, MODEL_PRESETS, BenchConfig, InferConfig, TrainConfig

__all__ = [
    "DTYPE_NAMES",
    "MODEL_PRESETS",
    "BenchConfig",
    "InferConfig",
    "TrainConfig",
]
