import torch
import torch.nn as nn
import torch.autograd as autograd

class kWTA(autograd.Function):
    @staticmethod
    def forward(ctx, x, k):
        # Get the k-th largest value along the last dimension
        # and create a mask for the top k values
        topk_values, _ = torch.topk(x, k, dim=-1, largest=True)
        
        # Get the minimum value among the top k values
        threshold = topk_values[..., -1].unsqueeze(-1)
        
        # Create a mask where values greater than or equal to the threshold are 1, others 0
        # This handles ties by including all values equal to the threshold
        mask = (x >= threshold).float()
        
        # Apply the mask to x
        output = x * mask
        
        ctx.save_for_backward(x, mask)
        return output

    @staticmethod
    def backward(ctx, grad_output):
        x, mask = ctx.saved_tensors
        
        # The gradient is passed through only for the activated neurons
        grad_input = grad_output * mask
        
        # k is a constant, so its gradient is None
        return grad_input, None

class KWTA(nn.Module):
    def __init__(self, k):
        super().__init__()
        self.k = k

    def forward(self, x):
        return kWTA.apply(x, self.k)
