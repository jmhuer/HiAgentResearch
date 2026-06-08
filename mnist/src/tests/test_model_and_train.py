import pytest
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from model import Autoencoder, Decoder, EnsembleMnistCNN, MnistCNN
from train import (
    MIN_LR_RATIO,
    evaluate_ensemble_accuracy,
    evaluate_ensemble_loss,
    learning_rate_for_step,
    pretrain_autoencoder,
)


def test_mnist_cnn_architecture():
    model = MnistCNN()
    output = model(torch.randn(1, 1, 28, 28))
    assert output.shape == (1, 10)


def test_decoder_architecture():
    decoder = Decoder()
    output = decoder(torch.randn(1, 64, 7, 7))
    assert output.shape == (1, 1, 28, 28)


def test_autoencoder_forward():
    autoencoder = Autoencoder(MnistCNN().features, Decoder())
    output = autoencoder(torch.randn(1, 1, 28, 28))
    assert output.shape == (1, 1, 28, 28)


def test_pretrain_autoencoder_loss_decrease():
    dummy_loader = DataLoader(TensorDataset(torch.randn(100, 1, 28, 28), torch.zeros(100)), batch_size=10)
    autoencoder = Autoencoder(MnistCNN().features, Decoder())
    device = torch.device("cpu")
    autoencoder.to(device)

    initial_loss = float("inf")
    for _ in range(3):
        optimizer = torch.optim.Adam(autoencoder.parameters(), lr=1e-3)
        criterion = nn.MSELoss()
        autoencoder.train()
        total_loss = 0.0
        for images, _ in dummy_loader:
            images = images.to(device)
            optimizer.zero_grad()
            loss = criterion(autoencoder(images), images)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        current_loss = total_loss / len(dummy_loader)
        assert current_loss < initial_loss
        initial_loss = current_loss


def test_learning_rate_for_step_cosine_floor():
    base_lr = 1e-3
    total_steps = 100
    warmup_steps = 10
    floor_lr = learning_rate_for_step(
        total_steps,
        total_steps=total_steps,
        warmup_steps=warmup_steps,
        base_lr=base_lr,
        min_lr_ratio=MIN_LR_RATIO,
    )
    assert floor_lr == pytest.approx(base_lr * MIN_LR_RATIO)
    last_train_lr = learning_rate_for_step(
        total_steps - 1,
        total_steps=total_steps,
        warmup_steps=warmup_steps,
        base_lr=base_lr,
        min_lr_ratio=MIN_LR_RATIO,
    )
    assert last_train_lr == pytest.approx(base_lr * MIN_LR_RATIO, rel=0.01)


def test_evaluate_ensemble_loss():
    loader = DataLoader(
        TensorDataset(torch.randn(32, 1, 28, 28), torch.randint(0, 10, (32,))),
        batch_size=16,
    )
    model = EnsembleMnistCNN(num_sub_networks=3, kwta_k=1)
    device = torch.device("cpu")
    criterion = nn.CrossEntropyLoss()

    loss = evaluate_ensemble_loss(model, loader, device, criterion)

    assert loss > 0.0


def test_evaluate_ensemble_accuracy():
    loader = DataLoader(
        TensorDataset(torch.randn(32, 1, 28, 28), torch.randint(0, 10, (32,))),
        batch_size=16,
    )
    model = EnsembleMnistCNN(num_sub_networks=3, kwta_k=1)
    device = torch.device("cpu")

    accuracy = evaluate_ensemble_accuracy(model, loader, device)

    assert 0.0 <= accuracy <= 1.0
