import torch
import torch.nn as nn
import segmentation_models_pytorch as smp



class UNetDictOut(nn.Module):
    """
    Wrap SMP UNet so your existing code can keep using:
        out = model(x)['out']
    """
    def __init__(self, in_ch=2, num_classes=2, encoder_name="densenet201",
                 encoder_weights="imagenet", dropout_p=0.0):
        super().__init__()
        self.net = smp.Unet(
            encoder_name=encoder_name,
            encoder_weights=encoder_weights,   # ImageNet pretrained
            in_channels=in_ch,           # <-- 2ch VV,VH
            classes=num_classes,               # <-- 2 classes
            activation=None                    # logits
        )
        # Optional dropout for MC-dropout later (set dropout_p>0 to enable)
        self.drop = nn.Dropout2d(dropout_p) if dropout_p and dropout_p > 0 else nn.Identity()

    def forward(self, x):
        logits = self.net(x)          # (B,C,H,W)
        logits = self.drop(logits)    # optional
        return {"out": logits}