#!/usr/bin/env python3
"""Train MNIST CNN and write metrics for the eval gate."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms

from model import MnistCNN, Decoder, Autoencoder
import optuna

def pretrain_autoencoder(autoencoder: Autoencoder, loader: DataLoader, device: torch.device, epochs: int, lr: float) -> None:
    # print("Starting autoencoder pre-training...")
    optimizer = torch.optim.Adam(autoencoder.parameters(), lr=lr)
    criterion = nn.MSELoss() # Using MSE for reconstruction loss

    autoencoder.train()
    for epoch in range(epochs):
        total_loss = 0
        for images, _ in loader: # No labels needed for unsupervised learning
            images = images.to(device)
            optimizer.zero_grad()
            reconstructions = autoencoder(images)
            loss = criterion(reconstructions, images)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        # print(f"Autoencoder Pre-train Epoch {epoch+1}/{epochs}, Loss: {total_loss/len(loader):.4f}")
    # print("Autoencoder pre-training finished.")


def _load_baseline(mnist_root: Path) -> dict:
    return json.loads((mnist_root / "baseline.json").read_text(encoding="utf-8"))


def _accuracy(model: nn.Module, loader: DataLoader, device: torch.device) -> float:
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


def _latency_ms(model: nn.Module, loader: DataLoader, device: torch.device, samples: int = 256) -> float:
    model.eval()
    seen = 0
    start = time.perf_counter()
    with torch.no_grad():
        for images, _ in loader:
            images = images.to(device)
            _ = model(images)
            seen += images.size(0)
            if seen >= samples:
                break
    elapsed = time.perf_counter() - start
    return (elapsed / max(seen, 1)) * 1000.0


class EnsembleMnistCNN(nn.Module):
    def __init__(self, num_sub_networks: int, kwta_k: int):
        super().__init__()
        self.models = nn.ModuleList([MnistCNN() for _ in range(num_sub_networks)])
        self.kwta_k = kwta_k

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        outputs = torch.stack([model(x) for model in self.models])
        # Apply k-Winners-Take-All
        topk_values, topk_indices = torch.topk(outputs, self.kwta_k, dim=0)
        # Create a mask for the top-k values
        mask = torch.zeros_like(outputs, dtype=torch.bool)
        mask.scatter_(0, topk_indices, True)
        # Zero out non-top-k values and sum the top-k
        kwta_output = torch.where(mask, outputs, torch.tensor(0.0, device=outputs.device))
        return kwta_output.sum(dim=0)


def objective(trial: optuna.Trial) -> float:
    parser = argparse.ArgumentParser(description="Train MNIST CNN.")
    parser.add_argument("--mnist-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--autoencoder-epochs", type=int, default=5, help="Number of epochs for autoencoder pre-training.")
    parser.add_argument("--num-sub-networks", type=int, default=3, help="Number of sub-networks in the ensemble.")
    parser.add_argument("--kwta-k", type=int, default=1, help="k value for k-Winners-Take-All.")
    parser.add_argument("--quick", action="store_true", help="Use a small train subset for fast smoke runs.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Model checkpoint path.")
    parser.add_argument("--output", type=Path, default=None, help="Output metrics file path.")
    args = parser.parse_args([]) # Pass empty list to prevent argparse from reading sys.argv

    # Hyperparameters to optimize
    lr = trial.suggest_float("lr", 1e-5, 1e-1, log=True)
    batch_size = trial.suggest_int("batch_size", 32, 256, step=32)
    epochs = trial.suggest_int("epochs", 1, 5) # Limiting epochs for faster trials

    mnist_root = args.mnist_root.resolve()
    device = torch.device(args.device)

    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
    data_dir = mnist_root / "data"
    train_set = datasets.MNIST(str(data_dir), train=True, download=True, transform=transform)
    test_set = datasets.MNIST(str(data_dir), train=False, download=True, transform=transform)
    if args.quick:
        train_set = Subset(train_set, range(2000))
        test_set = Subset(test_set, range(1000))

    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=0)
    test_loader = DataLoader(test_set, batch_size=batch_size, shuffle=False, num_workers=0)

    encoder_for_ae = MnistCNN().features
    decoder = Decoder().to(device)
    autoencoder = Autoencoder(encoder_for_ae, decoder).to(device)

    pretrain_autoencoder(autoencoder, train_loader, device, args.autoencoder_epochs, lr)

    ensemble_model = EnsembleMnistCNN(args.num_sub_networks, args.kwta_k).to(device)

    for i, model in enumerate(ensemble_model.models):
        model.features.load_state_dict(autoencoder.encoder.state_dict())
        # print(f"Loaded pre-trained encoder weights into MnistCNN sub-network {i+1}.")

        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        criterion = nn.CrossEntropyLoss()

        # print(f"Training sub-network {i+1}...")
        for epoch in range(epochs):
            model.train()
            for images, labels in train_loader:
                images = images.to(device)
                labels = labels.to(device)
                optimizer.zero_grad()
                loss = criterion(model(images), labels)
                loss.backward()
                optimizer.step()
            # print(f"Sub-network {i+1} Epoch {epoch+1}/{epochs} finished.")

    accuracy = _accuracy(ensemble_model, test_loader, device)
    # latency_ms = _latency_ms(ensemble_model, test_loader, device) # Latency is not part of optimization for now

    return accuracy


def main() -> None:
    parser = argparse.ArgumentParser(description="Train MNIST CNN with Optuna hyperparameter optimization.")
    parser.add_argument("--mnist-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--n-trials", type=int, default=10, help="Number of Optuna trials.")
    parser.add_argument("--quick", action="store_true", help="Use a small train subset for fast smoke runs.")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=args.n_trials)

    print("\nNumber of finished trials: ", len(study.trials))
    print("Best trial:")
    trial = study.best_trial

    print("  Value: ", trial.value)
    print("  Params: ")
    for key, value in trial.params.items():
        print(f"    {key}: {value}")

    # Here you would typically re-train the model with the best parameters
    # or save them for later use. For this task, we just print them.

if __name__ == "__main__":
    main()
