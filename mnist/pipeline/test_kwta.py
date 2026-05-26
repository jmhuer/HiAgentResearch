import torch
import torch.nn as nn
from kwta import KWTA, kWTA

def test_kwta_forward():
    # Test case 1: Simple 1D tensor
    x = torch.tensor([1.0, 5.0, 2.0, 8.0, 3.0, 6.0], dtype=torch.float32)
    k = 3
    kwta_module = KWTA(k)
    output = kwta_module(x)
    # Expected output: top 3 values are 8.0, 6.0, 5.0. Others are 0.
    # The order might change, but the values should be present.
    expected_output_values = {8.0, 6.0, 5.0}
    actual_output_values = {val.item() for val in output if val.item() != 0.0}
    assert actual_output_values == expected_output_values, f"Expected {expected_output_values}, got {actual_output_values}"
    assert torch.sum(output != 0) == k, f"Expected {k} non-zero values, got {torch.sum(output != 0)}"

    # Test case 2: 2D tensor
    x = torch.tensor([[1.0, 5.0, 2.0], [8.0, 3.0, 6.0]], dtype=torch.float32)
    k = 2
    kwta_module = KWTA(k)
    output = kwta_module(x)
    # Expected output: for each row, top 2 values are kept.
    # Row 1: 5.0, 2.0 (or 2.0, 5.0)
    # Row 2: 8.0, 6.0 (or 6.0, 8.0)
    expected_output_row1 = {5.0, 2.0}
    expected_output_row2 = {8.0, 6.0}
    actual_output_row1 = {val.item() for val in output[0] if val.item() != 0.0}
    actual_output_row2 = {val.item() for val in output[1] if val.item() != 0.0}
    assert actual_output_row1 == expected_output_row1, f"Expected {expected_output_row1}, got {actual_output_row1}"
    assert actual_output_row2 == expected_output_row2, f"Expected {expected_output_row2}, got {actual_output_row2}"
    assert torch.sum(output[0] != 0) == k and torch.sum(output[1] != 0) == k

def test_kwta_backward():
    # Test case 1: Simple 1D tensor
    x = torch.tensor([1.0, 5.0, 2.0, 8.0, 3.0, 6.0], dtype=torch.float32, requires_grad=True)
    k = 3
    kwta_module = KWTA(k)
    output = kwta_module(x)
    
    # Simulate a loss and backpropagate
    loss = output.sum()
    loss.backward()
    
    # Only the top-k values should have non-zero gradients
    # The original values were 8.0, 6.0, 5.0. Their indices are 3, 5, 1.
    expected_grad = torch.tensor([0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
    assert torch.allclose(x.grad, expected_grad), f"Expected grad {expected_grad}, got {x.grad}"

    # Test case 2: 2D tensor
    x = torch.tensor([[1.0, 5.0, 2.0], [8.0, 3.0, 6.0]], dtype=torch.float32, requires_grad=True)
    k = 2
    kwta_module = KWTA(k)
    output = kwta_module(x)

    loss = output.sum()
    loss.backward()

    # Expected gradients for each row
    # Row 1: 5.0, 2.0 were active. Indices 1, 2. Expected grad: [0.0, 1.0, 1.0]
    # Row 2: 8.0, 6.0 were active. Indices 0, 2. Expected grad: [1.0, 0.0, 1.0]
    expected_grad = torch.tensor([[0.0, 1.0, 1.0], [1.0, 0.0, 1.0]])
    assert torch.allclose(x.grad, expected_grad), f"Expected grad {expected_grad}, got {x.grad}"


if __name__ == '__main__':
    test_kwta_forward()
    test_kwta_backward()
    print("All kWTA tests passed!")
