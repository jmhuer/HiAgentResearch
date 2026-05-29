import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from model import Autoencoder, Decoder, MnistCNN
from train import pretrain_autoencoder


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
