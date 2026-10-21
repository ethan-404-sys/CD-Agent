"""6f-C small CNN and paired augmentation; exp5-env, torch 2.5.1/torchvision 0.20.1.

Estimate: module CPU RAM <0.5 GiB; training batch 256 uses roughly 2–5 GiB GPU
for early fusion, 3–7 GiB for siamese (unmeasured, including optimizer/activations).
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F
from torchvision.models.resnet import BasicBlock


class Encoder(nn.Module):
    """ResNet18 [2,2,2,2], channels 32/64/128/256, 3x3 stride-1 crop stem."""

    def __init__(self, channels, dropout):
        super().__init__()
        layers = [nn.Conv2d(channels, 32, 3, padding=1, bias=False),
                  nn.BatchNorm2d(32), nn.ReLU(inplace=True)]
        previous = 32
        for stage, width in enumerate((32, 64, 128, 256)):
            stride = 1 if stage == 0 else 2
            downsample = (nn.Sequential(nn.Conv2d(previous, width, 1, stride=stride, bias=False),
                                        nn.BatchNorm2d(width))
                          if stride != 1 or previous != width else None)
            layers.extend((BasicBlock(previous, width, stride, downsample),
                           BasicBlock(width, width), nn.Dropout2d(dropout / 2)))
            previous = width
        layers.extend((nn.AdaptiveAvgPool2d(1), nn.Flatten()))
        self.layers = nn.Sequential(*layers)

    def forward(self, x):
        return self.layers(x)


class SmallCNN(nn.Module):
    def __init__(self, architecture="early", dropout=0.5):
        super().__init__()
        if architecture not in ("early", "siamese"):
            raise ValueError(architecture)
        self.architecture = architecture
        self.encoder = Encoder(8 if architecture == "early" else 5, dropout)
        self.head = nn.Sequential(nn.Dropout(dropout),
                                  nn.Linear(256 if architecture == "early" else 768, 1))
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, mode="fan_out", nonlinearity="relu")
            elif isinstance(module, nn.BatchNorm2d):
                nn.init.ones_(module.weight)
                nn.init.zeros_(module.bias)
        # Residual branches start near identity.
        for module in self.modules():
            if isinstance(module, BasicBlock):
                nn.init.zeros_(module.bn2.weight)

    def forward(self, x):
        if self.architecture == "early":
            features = self.encoder(x)
        else:
            t1 = torch.cat((x[:, :3], x[:, 6:8]), dim=1)
            t2 = torch.cat((x[:, 3:6], x[:, 6:8]), dim=1)
            # One shared pass also gives both dates the same BatchNorm statistics.
            f1, f2 = self.encoder(torch.cat((t1, t2), dim=0)).chunk(2, dim=0)
            features = torch.cat((f1, f2, (f1 - f2).abs()), dim=1)
        return self.head(features).squeeze(1)


def prepare_inputs(crops, augment=False):
    x = crops.float().div_(255)
    if augment:
        n, device = len(x), x.device
        quarter = torch.randint(0, 4, (n,), device=device)
        # Exact quarter-turn matrices avoid rounding a nominal 90-degree angle.
        cos = x.new_tensor([1, 0, -1, 0])[quarter]
        sin = x.new_tensor([0, 1, 0, -1])[quarter]
        signs = torch.randint(0, 2, (n, 2), device=device).mul(2).sub(1)
        scale = torch.empty(n, device=device).uniform_(0.9, 1.1)
        theta = x.new_zeros(n, 2, 3)
        theta[:, 0, 0] = cos * signs[:, 0] / scale
        theta[:, 0, 1] = -sin * signs[:, 1] / scale
        theta[:, 1, 0] = sin * signs[:, 0] / scale
        theta[:, 1, 1] = cos * signs[:, 1] / scale
        grid = F.affine_grid(theta, x.shape, align_corners=False)
        interpolated = F.grid_sample(x, grid, mode="bilinear", padding_mode="border",
                                     align_corners=False)
        interpolated[:, 6:7] = F.grid_sample(x[:, 6:7], grid, mode="nearest",
                                            padding_mode="zeros", align_corners=False)
        x = interpolated
        # Geometry is shared; mild color jitter is independently sampled per date.
        for start in (0, 3):
            rgb = x[:, start:start + 3]
            brightness = x.new_empty(n, 1, 1, 1).uniform_(0.9, 1.1)
            contrast = x.new_empty(n, 1, 1, 1).uniform_(0.9, 1.1)
            saturation = x.new_empty(n, 1, 1, 1).uniform_(0.95, 1.05)
            rgb = rgb * brightness
            gray = (rgb * x.new_tensor([0.2989, 0.5870, 0.1140])[None, :, None, None]).sum(
                dim=1, keepdim=True)
            rgb = (rgb - gray.mean((2, 3), keepdim=True)) * contrast + gray.mean(
                (2, 3), keepdim=True)
            rgb = (rgb - gray) * saturation + gray
            x[:, start:start + 3] = rgb.clamp(0, 1)
    # From-scratch input scale; mask/probability remain [0,1].
    x[:, :6] = (x[:, :6] - 0.5) / 0.5
    return x
