from __future__ import annotations

import torch
import torch.nn as nn

from .config import MODEL_PRESETS


class MLP(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int = 1):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.net(features)


def count_parameters(model: nn.Module) -> int:
    seen: set[int] = set()
    total = 0
    for parameter in model.parameters():
        if id(parameter) not in seen:
            seen.add(id(parameter))
            total += parameter.numel()
    return total


def forward_flops_per_sample(model: nn.Module) -> int:
    flops = 0
    for module in model.modules():
        if isinstance(module, nn.Linear):
            flops += 2 * module.in_features * module.out_features
    return flops


def build_model(preset: str, input_dim: int, output_dim: int = 1) -> MLP:
    if preset not in MODEL_PRESETS:
        raise ValueError(f"unknown preset {preset!r}, expected one of {sorted(MODEL_PRESETS)}")
    preset_input_dim, hidden_dim = MODEL_PRESETS[preset]
    resolved_input_dim = input_dim or preset_input_dim
    return MLP(resolved_input_dim, hidden_dim, output_dim)
