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
# augmentation__a2 consolidated CoarseDropout (3eeb30a): single hole, 11–21% size, p=0.15.
COARSE_DROPOUT_NUM_HOLES_RANGE = (1, 1)
COARSE_DROPOUT_HOLE_HEIGHT_RANGE = (0.11, 0.21)
COARSE_DROPOUT_HOLE_WIDTH_RANGE = (0.11, 0.21)
COARSE_DROPOUT_FILL = 0
COARSE_DROPOUT_P = 0.15


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
    """Train-time augmentation with always-on consolidated CoarseDropout cutout.

    augmentation__a2 (3eeb30a): single hole, 11–21% of image, fill=0, p=0.15.
    No phase gating — every training sample may receive cutout (unlike pre-3eeb30a a2).
    """

    def __init__(self) -> None:
        self.aug = A.Compose(
            [
                A.Rotate(limit=15, p=0.5),
                A.ShiftScaleRotate(
                    shift_limit=0.1, scale_limit=0.1, rotate_limit=15, p=0.5
                ),
                A.RandomBrightnessContrast(p=0.2),
                A.CoarseDropout(
                    num_holes_range=COARSE_DROPOUT_NUM_HOLES_RANGE,
                    hole_height_range=COARSE_DROPOUT_HOLE_HEIGHT_RANGE,
                    hole_width_range=COARSE_DROPOUT_HOLE_WIDTH_RANGE,
                    fill=COARSE_DROPOUT_FILL,
                    p=COARSE_DROPOUT_P,
                ),
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


def build_eval_transform() -> transforms.Compose:
    """Deterministic preprocessing aligned with `.hiagentresearch/eval/score.py`."""
    return transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,)),
        ]
    )


def build_mnist_dataloaders(
    data_dir: Path,
    batch_size: int,
    *,
    quick: bool = False,
) -> tuple[DataLoader, DataLoader]:
    """Build train/val loaders: augmented train set, eval-aligned deterministic val set."""
    train_transform = build_mnist_transform()
    eval_transform = build_eval_transform()
    train_set = datasets.MNIST(str(data_dir), train=True, download=True, transform=train_transform)
    test_set = datasets.MNIST(str(data_dir), train=False, download=True, transform=eval_transform)
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


def evaluate_ensemble_accuracy(
    model: EnsembleMnistCNN,
    loader: DataLoader,
    device: torch.device,
) -> float:
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            preds = model(images).argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
    return correct / max(total, 1)


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
    """Train ensemble with merged hyperparameters__a1 + augmentation__a2 stack.

    a1: manual ``learning_rate_for_step`` with ``MIN_LR_RATIO`` cosine floor.
    a2: AdamW optimizer, label smoothing on training loss, decoupled weight decay.
    augmentation__a2: always-on consolidated CoarseDropout on train loader only
    (``COARSE_DROPOUT_*`` constants); val loader uses deterministic eval-aligned transform.
    Early stopping tracks validation accuracy with patience-bounded stopping and restores
    the best-seen weights before checkpoint export.
    """
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    train_criterion = nn.CrossEntropyLoss(label_smoothing=label_smoothing)

    best_val_accuracy = 0.0
    best_state_dict: dict[str, torch.Tensor] | None = None
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
                    min_lr_ratio=MIN_LR_RATIO,
                ),
            )
            images = images.to(device)
            labels = labels.to(device)
            optimizer.zero_grad()
            loss = train_criterion(model(images), labels)
            loss.backward()
            optimizer.step()
            global_step += 1

        val_accuracy = evaluate_ensemble_accuracy(model, test_loader, device)
        print(
            f"Ensemble Epoch {epoch + 1}/{epochs} finished. "
            f"Validation Accuracy: {val_accuracy:.4f}"
        )

        if val_accuracy > best_val_accuracy:
            best_val_accuracy = val_accuracy
            best_state_dict = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(
                    f"Early stopping ensemble due to no improvement in validation accuracy "
                    f"for {patience} epochs."
                )
                break

    if best_state_dict is not None:
        model.load_state_dict(best_state_dict)
        print(f"Restored best validation accuracy checkpoint ({best_val_accuracy:.4f}).")


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
        "epochs_budget": args.epochs,
        "early_stopping_metric": "val_accuracy",
        "patience": args.patience,
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
        "augmentation": {
            "cutout": "CoarseDropout",
            "num_holes_range": list(COARSE_DROPOUT_NUM_HOLES_RANGE),
            "hole_height_range": list(COARSE_DROPOUT_HOLE_HEIGHT_RANGE),
            "hole_width_range": list(COARSE_DROPOUT_HOLE_WIDTH_RANGE),
            "fill": COARSE_DROPOUT_FILL,
            "p": COARSE_DROPOUT_P,
            "val_transform": "deterministic_eval_aligned",
        },
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
    parser.add_argument(
        "--epochs",
        type=int,
        default=25,
        help="Maximum ensemble training epochs; early stopping may finish sooner.",
    )
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
        default=1e-3,
        help="Decoupled weight decay for AdamW (calibrated for MIN_LR_RATIO=0.1 schedule).",
    )
    parser.add_argument(
        "--label-smoothing",
        type=float,
        default=0.05,
        help="Label smoothing epsilon for ensemble training CrossEntropyLoss.",
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
