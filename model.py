import torch
from torchsummary import summary
from torch_intermediate_layer_getter import IntermediateLayerGetter as MidGetter

model = torch.load("data/weights/yolopv2.pt", map_location=torch.device('cpu'))
model.eval()
# model = scripted_model.to_torchscript()

# Call torchsummary.summary
# summary(model, input_size=(3, 224, 224))

# print(model)


for name, param in model.named_parameters():
    print(f"{name}: {param.shape}")

# return_layers = {
#     'model.113.conv': 'model.113.conv',
#     'nested.0.1': 'nested',
#     'interaction_idty': 'interaction',
# }


# mid_getter = MidGetter(model, return_layers=return_layers, keep_output=True)
# mid_outputs, model_output = mid_getter(torch.randn(1, 2))

# print(model_output)