#!/usr/bin/env python3
"""Evaluate trained MNIST model and check gate thresholds against baseline."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, transforms
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix

# Import from sibling pipeline package path when run as script.
_PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))
from model import MnistCNN, EnsembleMnistCNN  # noqa: E402


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _accuracy(model: torch.nn.Module, loader: DataLoader, device: torch.device) -> tuple[float, torch.Tensor, torch.Tensor]:
    model.eval()
    correct = 0
    total = 0
    all_preds = []
    all_labels = []
    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            labels = labels.to(device)
            preds = model(images).argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
            all_preds.append(preds)
            all_labels.append(labels)
    return correct / max(total, 1), torch.cat(all_preds), torch.cat(all_labels)

def _precision_recall_f1(labels: torch.Tensor, preds: torch.Tensor) -> dict:
    precision, recall, f1, _ = precision_recall_fscore_support(labels.cpu().numpy(), preds.cpu().numpy(), average='macro', zero_division=0)
    return {"precision": precision, "recall": recall, "f1_score": f1}

def _confusion_matrix(labels: torch.Tensor, preds: torch.Tensor) -> list[list[int]]:
    return confusion_matrix(labels.cpu().numpy(), preds.cpu().numpy()).tolist()


def _latency_ms(model: torch.nn.Module, loader: DataLoader, device: torch.device, samples: int = 256) -> float:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate MNIST pipeline outputs.")
    parser.add_argument("--mnist-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--metrics",
        type=Path,
        default=None,
        help="Optional train metrics JSON (default: pipeline/last_train_metrics.json).",
    )
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--quick", action="store_true", help="Evaluate on a small test subset.")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    mnist_root = args.mnist_root.resolve()
    baseline = _load_json(mnist_root / "baseline.json")
    metrics_path = args.metrics or (mnist_root / "pipeline" / "last_train_metrics.json")
    train_metrics = _load_json(metrics_path) if metrics_path.exists() else {}

    checkpoint_path = args.checkpoint or (mnist_root / train_metrics.get("checkpoint", "pipeline/checkpoints/mnist_cnn_ensemble.pt"))
    if not Path(checkpoint_path).is_absolute():
        checkpoint_path = mnist_root / checkpoint_path
    if not checkpoint_path.exists():
        print(json.dumps({"passed": False, "error": f"missing checkpoint: {checkpoint_path}"}, indent=2))
        return 1

    device = torch.device(args.device)
    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
    data_dir = mnist_root / "data"
    test_set = datasets.MNIST(str(data_dir), train=False, download=True, transform=transform)
    if args.quick:
        test_set = Subset(test_set, range(1000))
    test_loader = DataLoader(test_set, batch_size=128, shuffle=False, num_workers=0)

    num_sub_networks = train_metrics.get("num_sub_networks", 3)
    kwta_k = train_metrics.get("kwta_k", 1)

    model = EnsembleMnistCNN(num_sub_networks, kwta_k).to(device)
    payload = torch.load(checkpoint_path, map_location=device, weights_only=True)
    model.load_state_dict(payload["model_state_dict"])

    accuracy, all_preds, all_labels = _accuracy(model, test_loader, device)
    latency_ms = _latency_ms(model, test_loader, device)
    pr_f1_metrics = _precision_recall_f1(all_labels, all_preds)
    conf_matrix = _confusion_matrix(all_labels, all_preds)

    acc_ok = accuracy >= float(baseline["accuracy"])
    lat_ok = latency_ms <= float(baseline["latency_ms"])
    passed = acc_ok and lat_ok
    report = {
        "passed": passed,
        "accuracy": round(accuracy, 6),
        "latency_ms": round(latency_ms, 4),
        "precision": round(pr_f1_metrics["precision"], 6),
        "recall": round(pr_f1_metrics["recall"], 6),
        "f1_score": round(pr_f1_metrics["f1_score"], 6),
        "confusion_matrix": conf_matrix,
        "accuracy_ok": acc_ok,
        "latency_ok": lat_ok,
        "baseline": baseline,
        "train_metrics": train_metrics,
        "checkpoint": str(checkpoint_path.relative_to(mnist_root)),
    }
    print(json.dumps(report, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
