import torch

from kwta import KWTA


def test_kwta_forward():
    x = torch.tensor([1.0, 5.0, 2.0, 8.0, 3.0, 6.0], dtype=torch.float32)
    k = 3
    kwta_module = KWTA(k)
    output = kwta_module(x)
    expected_output_values = {8.0, 6.0, 5.0}
    actual_output_values = {val.item() for val in output if val.item() != 0.0}
    assert actual_output_values == expected_output_values
    assert torch.sum(output != 0) == k

    x = torch.tensor([[1.0, 5.0, 2.0], [8.0, 3.0, 6.0]], dtype=torch.float32)
    k = 2
    kwta_module = KWTA(k)
    output = kwta_module(x)
    expected_output_row1 = {5.0, 2.0}
    expected_output_row2 = {8.0, 6.0}
    actual_output_row1 = {val.item() for val in output[0] if val.item() != 0.0}
    actual_output_row2 = {val.item() for val in output[1] if val.item() != 0.0}
    assert actual_output_row1 == expected_output_row1
    assert actual_output_row2 == expected_output_row2
    assert torch.sum(output[0] != 0) == k and torch.sum(output[1] != 0) == k


def test_kwta_backward():
    x = torch.tensor([1.0, 5.0, 2.0, 8.0, 3.0, 6.0], dtype=torch.float32, requires_grad=True)
    k = 3
    kwta_module = KWTA(k)
    output = kwta_module(x)
    output.sum().backward()
    expected_grad = torch.tensor([0.0, 1.0, 0.0, 1.0, 0.0, 1.0])
    assert torch.allclose(x.grad, expected_grad)

    x = torch.tensor([[1.0, 5.0, 2.0], [8.0, 3.0, 6.0]], dtype=torch.float32, requires_grad=True)
    k = 2
    kwta_module = KWTA(k)
    output = kwta_module(x)
    output.sum().backward()
    expected_grad = torch.tensor([[0.0, 1.0, 1.0], [1.0, 0.0, 1.0]])
    assert torch.allclose(x.grad, expected_grad)
