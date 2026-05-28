import torch
import torch.nn as nn
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from model import EnsembleMnistCNN

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

    batch_size = 1
    dummy_input = torch.randn(batch_size, 1, 28, 28)

    class ConstantHead(nn.Module):
        def __init__(self):
            super().__init__()

        def forward(self, x):
            return torch.tensor(
                [
                    [[0.1, 0.9, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                    [[0.8, 0.1, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                    [[0.2, 0.3, 0.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
                ],
                dtype=x.dtype,
                device=x.device,
            )

    ensemble.trunk = nn.Identity()
    ensemble.head = ConstantHead()

    output = ensemble(dummy_input)

    # Current ensemble sums the top-k logits per class.
    expected_output = torch.tensor([[1.0, 1.2, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]])
    
    assert torch.allclose(output, expected_output, atol=1e-5)

