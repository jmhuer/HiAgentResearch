import torch
import torch.nn as nn
from pipeline.model import MnistCNN, EnsembleMnistCNN

def test_ensemble_output_shape():
    num_models = 3
    k = 1
    ensemble = EnsembleMnistCNN(num_models, k)
    
    # Create a dummy input
    batch_size = 16
    dummy_input = torch.randn(batch_size, 1, 28, 28)
    
    output = ensemble(dummy_input)
    
    # The output shape should be (batch_size, num_classes), where num_classes is 10 for MNIST
    assert output.shape == (batch_size, 10)

def test_ensemble_kwta_logic():
    num_models = 3
    k = 2
    ensemble = EnsembleMnistCNN(num_models, k)
    
    # Manually set the weights of the sub-models for predictable output
    # This is a simplified example, in reality, weights would be trained
    for model in ensemble.models:
        # Set all weights to 0 except for one to make predictions clear
        for param in model.parameters():
            nn.init.constant_(param, 0.0)
    
    # Create a dummy input
    batch_size = 1
    dummy_input = torch.randn(batch_size, 1, 28, 28)
    
    # Manually set outputs for each sub-model for a single input
    # Model 1: [0.1, 0.9, 0.2, ...] -> class 1
    # Model 2: [0.8, 0.1, 0.3, ...] -> class 0
    # Model 3: [0.2, 0.3, 0.7, ...] -> class 2
    
    # Override the forward method of sub-models for testing specific outputs
    class MockMnistCNN(MnistCNN):
        def __init__(self, output_values):
            super().__init__()
            self._output_values = output_values
        
        def forward(self, x):
            return torch.tensor(self._output_values).unsqueeze(0)

    ensemble.models = nn.ModuleList([
        MockMnistCNN([0.1, 0.9, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        MockMnistCNN([0.8, 0.1, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        MockMnistCNN([0.2, 0.3, 0.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ])

    output = ensemble(dummy_input)
    
    # Expected kWTA logic (k=2):
    # For class 0: top 2 are 0.8 (model 2) and 0.2 (model 3). Average = (0.8 + 0.2) / 2 = 0.5
    # For class 1: top 2 are 0.9 (model 1) and 0.3 (model 3). Average = (0.9 + 0.3) / 2 = 0.6
    # For class 2: top 2 are 0.7 (model 3) and 0.3 (model 2). Average = (0.7 + 0.3) / 2 = 0.5
    
    expected_output = torch.tensor([[0.5, 0.6, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
    
    assert torch.allclose(output, expected_output, atol=1e-5)

