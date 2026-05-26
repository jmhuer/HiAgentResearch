import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from model import MnistCNN, Decoder, Autoencoder
from train import pretrain_autoencoder # Import the pretrain function

def test_mnist_cnn_architecture():
    model = MnistCNN()
    # Create a dummy input tensor (batch_size, channels, height, width)
    input_tensor = torch.randn(1, 1, 28, 28)
    output = model(input_tensor)
    # Expected output for MNIST is 10 classes
    assert output.shape == (1, 10), f"Expected output shape (1, 10), but got {output.shape}"
    print("MnistCNN architecture test passed.")

def test_decoder_architecture():
    decoder = Decoder()
    # Input to decoder is the output of the encoder (64x7x7)
    input_tensor = torch.randn(1, 64, 7, 7)
    output = decoder(input_tensor)
    # Expected output is the original image size (1x28x28)
    assert output.shape == (1, 1, 28, 28), f"Expected output shape (1, 1, 28, 28), but got {output.shape}"
    print("Decoder architecture test passed.")

def test_autoencoder_forward():
    encoder_for_ae = MnistCNN().features
    decoder = Decoder()
    autoencoder = Autoencoder(encoder_for_ae, decoder)
    input_tensor = torch.randn(1, 1, 28, 28)
    output = autoencoder(input_tensor)
    assert output.shape == (1, 1, 28, 28), f"Expected output shape (1, 1, 28, 28), but got {output.shape}"
    print("Autoencoder forward pass test passed.")

def test_pretrain_autoencoder_loss_decrease():
    # Create dummy data
    dummy_images = torch.randn(100, 1, 28, 28)
    dummy_dataset = TensorDataset(dummy_images, torch.zeros(100))
    dummy_loader = DataLoader(dummy_dataset, batch_size=10)

    encoder_for_ae = MnistCNN().features
    decoder = Decoder()
    autoencoder = Autoencoder(encoder_for_ae, decoder)
    device = torch.device("cpu") # Use CPU for testing
    autoencoder.to(device)

    # Run pre-training for a few epochs and check if loss decreases
    initial_loss = float('inf')
    for epoch in range(3): # Run for 3 epochs
        optimizer = torch.optim.Adam(autoencoder.parameters(), lr=1e-3)
        criterion = nn.MSELoss()
        autoencoder.train()
        total_loss = 0
        for images, _ in dummy_loader:
            images = images.to(device)
            optimizer.zero_grad()
            reconstructions = autoencoder(images)
            loss = criterion(reconstructions, images)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        current_loss = total_loss / len(dummy_loader)
        print(f"Epoch {epoch+1}, Loss: {current_loss:.4f}")
        assert current_loss < initial_loss, f"Loss did not decrease in epoch {epoch+1}"
        initial_loss = current_loss
    print("Autoencoder pre-training loss decrease test passed.")

if __name__ == '__main__':
    test_mnist_cnn_architecture()
    test_decoder_architecture()
    test_autoencoder_forward()
    test_pretrain_autoencoder_loss_decrease()
    print("All model and training tests passed!")
