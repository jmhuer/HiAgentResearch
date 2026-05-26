"""Small CNN for MNIST classification."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from kwta import KWTA


class Autoencoder(nn.Module):
    def __init__(self, encoder, decoder) -> None:
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder

    def forward(self, x):
        encoded = self.encoder(x)
        decoded = self.decoder(encoded)
        return decoded


class Decoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.decoder = nn.Sequential(
            nn.ConvTranspose2d(64, 32, kernel_size=3, stride=2, padding=1, output_padding=1),
            KWTA(k=10),
            nn.ConvTranspose2d(32, 1, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.Sigmoid() # Output pixel values between 0 and 1
        )

    def forward(self, x):
        return self.decoder(x)


class MnistEncoder(nn.Module):
    """Autoencoder encoder; outputs 64x7x7 for Decoder."""

    def __init__(self) -> None:
        super().__init__()
        self.in_planes = 64
        block = BasicBlock
        self.conv1 = nn.Conv2d(1, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.kwta1 = KWTA(k=10)
        self.layer1 = self._make_layer(block, 64, 2, stride=1, activation="kwta")
        self.layer2 = self._make_layer(block, 128, 2, stride=2, activation="kwta")
        self.proj = nn.Conv2d(128, 64, kernel_size=3, stride=2, padding=1, bias=False)
        self.bn_proj = nn.BatchNorm2d(64)

    def _make_layer(self, block, planes, num_blocks, stride, activation="kwta"):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for stride in strides:
            layers.append(block(self.in_planes, planes, stride, activation=activation))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = self.kwta1(self.bn1(self.conv1(x)))
        out = self.layer1(out)
        out = self.layer2(out)
        return F.relu(self.bn_proj(self.proj(out)))


class MnistCNN(nn.Module):

    def __init__(self) -> None:
        super().__init__()
        self.resnet = ResNet18()
        self.features = MnistEncoder()

    def forward(self, x):
        return self.resnet(x)


def _stage_activation(name: str) -> nn.Module:
    if name == "kwta":
        return KWTA(k=10)
    if name == "relu":
        return nn.ReLU(inplace=True)
    raise ValueError(f"unknown activation: {name}")


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, in_planes, planes, stride=1, activation: str = "kwta"):
        super(BasicBlock, self).__init__()
        self.conv1 = nn.Conv2d(in_planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.act1 = _stage_activation(activation)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.act2 = _stage_activation(activation)

        self.shortcut = nn.Sequential()
        if stride != 1 or in_planes != self.expansion * planes:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_planes, self.expansion * planes, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(self.expansion * planes)
            )

    def forward(self, x):
        out = self.act1(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out += self.shortcut(x)
        out = self.act2(out)
        return out


class ResNet(nn.Module):
    def __init__(self, block, num_blocks, num_classes=10):
        super(ResNet, self).__init__()
        self.in_planes = 64

        self.conv1 = nn.Conv2d(1, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.kwta1 = KWTA(k=10)
        self.layer1 = self._make_layer(block, 64, num_blocks[0], stride=1, activation="kwta")
        self.layer2 = self._make_layer(block, 128, num_blocks[1], stride=2, activation="kwta")
        self.layer3 = self._make_layer(block, 256, num_blocks[2], stride=2, activation="relu")
        self.layer4 = self._make_layer(block, 512, num_blocks[3], stride=2, activation="relu")
        self.linear = nn.Linear(512 * block.expansion, num_classes)

    def _make_layer(self, block, planes, num_blocks, stride, activation="kwta"):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for stride in strides:
            layers.append(block(self.in_planes, planes, stride, activation=activation))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = self.kwta1(self.bn1(self.conv1(x)))
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        out = self.layer4(out)
        out = nn.functional.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        out = self.linear(out)
        return out

def ResNet18():
    return ResNet(BasicBlock, [2, 2, 2, 2])


class ResNetTrunk(nn.Module):
    """Shared backbone through layer3 (256x7x7)."""

    def __init__(self, block, num_blocks) -> None:
        super().__init__()
        self.in_planes = 64
        self.conv1 = nn.Conv2d(1, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.kwta1 = KWTA(k=10)
        self.layer1 = self._make_layer(block, 64, num_blocks[0], stride=1, activation="kwta")
        self.layer2 = self._make_layer(block, 128, num_blocks[1], stride=2, activation="kwta")
        self.layer3 = self._make_layer(block, 256, num_blocks[2], stride=2, activation="relu")

    def _make_layer(self, block, planes, num_blocks, stride, activation="kwta"):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for stride in strides:
            layers.append(block(self.in_planes, planes, stride, activation=activation))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = self.kwta1(self.bn1(self.conv1(x)))
        out = self.layer1(out)
        out = self.layer2(out)
        return self.layer3(out)


class ResNetHead(nn.Module):
    """Per-ensemble-member layer4 + classifier head."""

    def __init__(self, block, num_blocks_layer4: int, num_classes: int = 10) -> None:
        super().__init__()
        self.in_planes = 256
        self.layer4 = self._make_layer(block, 512, num_blocks_layer4, stride=2, activation="relu")
        self.linear = nn.Linear(512 * block.expansion, num_classes)

    def _make_layer(self, block, planes, num_blocks, stride, activation="relu"):
        strides = [stride] + [1] * (num_blocks - 1)
        layers = []
        for stride in strides:
            layers.append(block(self.in_planes, planes, stride, activation=activation))
            self.in_planes = planes * block.expansion
        return nn.Sequential(*layers)

    def forward(self, x):
        out = self.layer4(x)
        out = F.avg_pool2d(out, 4)
        out = out.view(out.size(0), -1)
        return self.linear(out)


class EnsembleMnistCNN(nn.Module):
    """Shared-trunk ensemble: one layer3 trunk, multiple layer4 heads."""

    def __init__(self, num_sub_networks: int, kwta_k: int) -> None:
        super().__init__()
        block = BasicBlock
        num_blocks = [2, 2, 2, 2]
        self.trunk = ResNetTrunk(block, num_blocks[:3])
        self.heads = nn.ModuleList(
            [ResNetHead(block, num_blocks[3]) for _ in range(num_sub_networks)]
        )
        self.kwta_k = kwta_k

    def load_encoder_weights(self, encoder_state: dict) -> None:
        trunk_state = self.trunk.state_dict()
        for key, value in encoder_state.items():
            if key in trunk_state and trunk_state[key].shape == value.shape:
                trunk_state[key] = value
        self.trunk.load_state_dict(trunk_state)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        shared = self.trunk(x)
        outputs = torch.stack([head(shared) for head in self.heads])
        _, topk_indices = torch.topk(outputs, self.kwta_k, dim=0)
        mask = torch.zeros_like(outputs, dtype=torch.bool)
        mask.scatter_(0, topk_indices, True)
        kwta_output = torch.where(mask, outputs, torch.tensor(0.0, device=outputs.device))
        return kwta_output.sum(dim=0)


