#!/usr/bin/env python3
"""Train MNIST ensemble and write a checkpoint manifest for the frozen scorer.

Authoritative accuracy and latency come from `.hiagentresearch/eval/score.py`;
this script only trains and records checkpoint metadata.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import albumentations as A
import numpy as np
import torch
import torch.nn as nn
from albumentations.pytorch import ToTensorV2
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

try:
    from .model import Autoencoder, Decoder, EnsembleMnistCNN, MnistCNN
except ImportError:  # pragma: no cover - supports direct script/test execution.
    from model import Autoencoder, Decoder, EnsembleMnistCNN, MnistCNN

TRAINING_SEED = 42
MIN_LR_RATIO = 0.1


def learning_rate_for_step(
    step: int,
    *,
    total_steps: int,
    warmup_steps: int,
    base_lr: float,
    min_lr_ratio: float = MIN_LR_RATIO,
) -> float:
    """Linear warmup over the first epoch, then cosine decay through the planned budget."""
    min_lr = base_lr * min_lr_ratio
    if warmup_steps > 0 and step < warmup_steps:
        return base_lr * float(step + 1) / float(warmup_steps)
    if total_steps <= warmup_steps:
        return base_lr
    progress = (step - warmup_steps) / float(total_steps - warmup_steps)
    cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
    return min_lr + (base_lr - min_lr) * cosine


def set_optimizer_lr(optimizer: torch.optim.Optimizer, lr: float) -> None:
    for param_group in optimizer.param_groups:
        param_group["lr"] = lr


def set_training_seed(seed: int = TRAINING_SEED) -> None:
    """Fix Python, NumPy, and PyTorch RNGs for reproducible training runs."""
    torch.manual_seed(seed)
    np.random.seed(seed)


class AlbumentationsTransform:
    def __init__(self) -> None:
        self.aug = A.Compose(
            [
                A.Rotate(limit=15, p=0.5),
                A.ShiftScaleRotate(
                    shift_limit=0.1, scale_limit=0.1, rotate_limit=15, p=0.5
                ),
                A.RandomBrightnessContrast(p=0.2),
                ToTensorV2(),
            ]
        )

    def __call__(self, img):
        img = np.array(img)
        out = self.aug(image=img)["image"]
        if out.dtype == torch.uint8:
            out = out.float().div(255.0)
        return out


def build_mnist_transform() -> transforms.Compose:
    return transforms.Compose(
        [
            AlbumentationsTransform(),
            transforms.Normalize((0.1307,), (0.3081,)),
        ]
    )


def build_mnist_dataloaders(
    data_dir: Path,
    batch_size: int,
    *,
    quick: bool = False,
) -> tuple[DataLoader, DataLoader]:
    transform = build_mnist_transform()
    train_set = datasets.MNIST(str(data_dir), train=True, download=True, transform=transform)
    test_set = datasets.MNIST(str(data_dir), train=False, download=True, transform=transform)
    if quick:
        train_set = Subset(train_set, range(2000))
        test_set = Subset(test_set, range(1000))

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=0)
    test_loader = DataLoader(test_set, batch_size=batch_size, shuffle=False, num_workers=0)
    return train_loader, test_loader


def pretrain_autoencoder(
    autoencoder: Autoencoder,
    loader: DataLoader,
    device: torch.device,
    epochs: int,
    lr: float,
) -> None:
    print("Starting autoencoder pre-training...")
    optimizer = torch.optim.Adam(autoencoder.parameters(), lr=lr)
    criterion = nn.MSELoss()

    steps_per_epoch = len(loader)
    warmup_steps = steps_per_epoch
    total_steps = epochs * steps_per_epoch
    global_step = 0

    autoencoder.train()
    for epoch in range(epochs):
        total_loss = 0.0
        for images, _ in loader:
            set_optimizer_lr(
                optimizer,
                learning_rate_for_step(
                    global_step,
                    total_steps=total_steps,
                    warmup_steps=warmup_steps,
                    base_lr=lr,
                ),
            )
            images = images.to(device)
            optimizer.zero_grad()
            reconstructions = autoencoder(images)
            loss = criterion(reconstructions, images)
            loss.backward()
            optimizer.step()
            global_step += 1
            total_loss += loss.item()
        print(f"Autoencoder Pre-train Epoch {epoch + 1}/{epochs}, Loss: {total_loss / len(loader):.4f}")
    print("Autoencoder pre-training finished.")


def evaluate_ensemble_loss(
    model: EnsembleMnistCNN,
    loader: DataLoader,
    device: torch.device,
    criterion: nn.Module,
) -> float:
    model.eval()
    val_loss = 0.0
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            val_loss += criterion(model(images), labels).item()
    return val_loss / len(loader)


def train_ensemble_with_early_stopping(
    model: EnsembleMnistCNN,
    train_loader: DataLoader,
    test_loader: DataLoader,
    device: torch.device,
    *,
    epochs: int,
    lr: float,
    weight_decay: float,
    label_smoothing: float,
    patience: int,
) -> None:
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    criterion = nn.CrossEntropyLoss(label_smoothing=label_smoothing)

    best_val_loss = float("inf")
    patience_counter = 0

    steps_per_epoch = len(train_loader)
    warmup_steps = steps_per_epoch
    total_steps = epochs * steps_per_epoch
    global_step = 0

    print("Training shared-trunk ensemble...")
    for epoch in range(epochs):
        model.train()
        for images, labels in train_loader:
            set_optimizer_lr(
                optimizer,
                learning_rate_for_step(
                    global_step,
                    total_steps=total_steps,
                    warmup_steps=warmup_steps,
                    base_lr=lr,
                ),
            )
            images = images.to(device)
            labels = labels.to(device)
            optimizer.zero_grad()
            loss = criterion(model(images), labels)
            loss.backward()
            optimizer.step()
            global_step += 1

        val_loss = evaluate_ensemble_loss(model, test_loader, device, criterion)
        print(f"Ensemble Epoch {epoch + 1}/{epochs} finished. Validation Loss: {val_loss:.4f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(
                    f"Early stopping ensemble due to no improvement in validation loss "
                    f"for {patience} epochs."
                )
                break


def save_ensemble_artifacts(
    model: EnsembleMnistCNN,
    checkpoint_path: Path,
    *,
    metrics: dict,
    output_path: Path | None = None,
) -> dict:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model_state_dict": model.state_dict()}, checkpoint_path)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


def run_training_pipeline(args: argparse.Namespace) -> dict:
    """Run the full MNIST ensemble training pipeline and return checkpoint metadata."""
    set_training_seed()

    mnist_root = args.mnist_root.resolve()
    device = torch.device(args.device)
    data_dir = mnist_root / "data"
    train_loader, test_loader = build_mnist_dataloaders(
        data_dir, args.batch_size, quick=args.quick
    )

    encoder_for_ae = MnistCNN().features
    decoder = Decoder().to(device)
    autoencoder = Autoencoder(encoder_for_ae, decoder).to(device)
    pretrain_autoencoder(autoencoder, train_loader, device, args.autoencoder_epochs, args.lr)

    ensemble_model = EnsembleMnistCNN(args.num_sub_networks, args.kwta_k).to(device)
    ensemble_model.load_encoder_weights(autoencoder.encoder.state_dict())
    print("Loaded pre-trained encoder weights into shared trunk.")

    train_ensemble_with_early_stopping(
        ensemble_model,
        train_loader,
        test_loader,
        device,
        epochs=args.epochs,
        lr=args.lr,
        weight_decay=args.weight_decay,
        label_smoothing=args.label_smoothing,
        patience=args.patience,
    )

    ckpt_dir = mnist_root / "src" / "checkpoints"
    checkpoint_path = args.checkpoint or (ckpt_dir / "mnist_cnn_ensemble.pt")
    metrics = {
        "epochs": args.epochs,
        "checkpoint": str(checkpoint_path.relative_to(mnist_root)),
        "device": str(device),
        "quick_mode": args.quick,
        "num_sub_networks": args.num_sub_networks,
        "kwta_k": args.kwta_k,
        "optimizer": "AdamW",
        "weight_decay": args.weight_decay,
        "label_smoothing": args.label_smoothing,
        "lr_schedule": "cosine_warmup",
        "warmup_epochs": 1,
        "min_lr_ratio": MIN_LR_RATIO,
    }
    return save_ensemble_artifacts(
        ensemble_model,
        checkpoint_path,
        metrics=metrics,
        output_path=args.output,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MNIST CNN.")
    parser.add_argument("--mnist-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--autoencoder-epochs",
        type=int,
        default=5,
        help="Number of epochs for autoencoder pre-training.",
    )
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument(
        "--weight-decay",
        type=float,
        default=1e-2,
        help="Decoupled weight decay for AdamW.",
    )
    parser.add_argument(
        "--label-smoothing",
        type=float,
        default=0.1,
        help="Label smoothing epsilon for ensemble CrossEntropyLoss.",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help="Number of epochs to wait for improvement before early stopping.",
    )
    parser.add_argument(
        "--num-sub-networks",
        type=int,
        default=3,
        help="Number of sub-networks in the ensemble.",
    )
    parser.add_argument("--kwta-k", type=int, default=1, help="k value for k-Winners-Take-All.")
    parser.add_argument("--quick", action="store_true", help="Use a small train subset for fast smoke runs.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Model checkpoint path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output metrics file path.")
    args = parser.parse_args()

    metrics = run_training_pipeline(args)
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
