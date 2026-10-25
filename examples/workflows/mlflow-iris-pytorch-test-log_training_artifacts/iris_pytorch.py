"""Iris network and fitted normalization saved with the MLflow PyTorch model."""

import torch
from torch import nn


class IrisClassifier(nn.Module):
    """Accept raw four-feature Iris samples and return three class logits."""

    def __init__(self, mean, scale):
        super().__init__()
        self.register_buffer("mean", torch.as_tensor(mean, dtype=torch.float32))
        self.register_buffer(
            "scale", torch.as_tensor(scale, dtype=torch.float32).clamp_min(1e-6)
        )
        self.network = nn.Sequential(nn.Linear(4, 16), nn.Tanh(), nn.Linear(16, 3))

    def forward(self, features):
        return self.network((features - self.mean) / self.scale)
