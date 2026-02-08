import torch, torch.nn as nn, math
from tqdm.auto import tqdm
from pathlib import Path

# --- U-Net (base=48) ---
class DoubleConv(nn.Module):
    def __init__(self,in_ch,out_ch):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(in_ch,out_ch,3,padding=1,bias=False),
            nn.BatchNorm2d(out_ch), nn.ReLU(inplace=True),
            nn.Conv2d(out_ch,out_ch,3,padding=1,bias=False),
            nn.BatchNorm2d(out_ch), nn.ReLU(inplace=True))
    def forward(self,x): return self.net(x)

class Down(nn.Module):
    def __init__(self,in_ch,out_ch):
        super().__init__()
        self.pool=nn.MaxPool2d(2); self.conv=DoubleConv(in_ch,out_ch)
    def forward(self,x): return self.conv(self.pool(x))

class Up(nn.Module):
    def __init__(self,in_ch,out_ch):
        super().__init__()
        self.up=nn.ConvTranspose2d(in_ch,in_ch//2,2,stride=2)
        self.conv=DoubleConv(in_ch,out_ch)
    def forward(self,x,skip):
        x=self.up(x)
        dh=skip.size(2)-x.size(2); dw=skip.size(3)-x.size(3)
        if dh or dw: x=nn.functional.pad(x,[dw//2,dw-dw//2,dh//2,dh-dh//2])
        x=torch.cat([skip,x],dim=1)
        return self.conv(x)

class UNet(nn.Module):
    def __init__(self,in_ch=2,out_ch=1,base=48):  #4th batch of tuniing
        super().__init__()
        self.inc=DoubleConv(in_ch,base)
        self.d1 =Down(base,base*2)
        self.d2 =Down(base*2,base*4)
        self.d3 =Down(base*4,base*8)
        self.bot=DoubleConv(base*8,base*16)
        self.u3 =Up(base*16,base*8)
        self.u2 =Up(base*8, base*4)
        self.u1 =Up(base*4, base*2)
        self.u0 =Up(base*2, base)
        self.out=nn.Conv2d(base,out_ch,1)
    def forward(self,x):
        x1=self.inc(x); x2=self.d1(x1); x3=self.d2(x2); x4=self.d3(x3)
        xb=self.bot(x4); x=self.u3(xb,x4); x=self.u2(x,x3); x=self.u1(x,x2); x=self.u0(x,x1)
        return self.out(x)


""" 
# --- loss/metrics (same as before) ---
class DiceLoss(nn.Module):
    def __init__(self, eps=1e-6): super().__init__(); self.eps=eps
    def forward(self, logits, y):
        p = torch.sigmoid(logits)
        num = 2*(p*y).sum(dim=(2,3)) + self.eps
        den = p.sum(dim=(2,3)) + y.sum(dim=(2,3)) + self.eps
        return 1 - (num/den).mean()

bce  = nn.BCEWithLogitsLoss()
dice = DiceLoss()
def criterion(logits, y): return 0.5*bce(logits,y) + 0.5*dice(logits,y)

def iou_f1_from_logits(logits, y, thr=0.5):
    with torch.no_grad():
        p = (torch.sigmoid(logits) >= thr).float()
        inter = (p*y).sum(dim=(2,3))
        union = (p + y - p*y).sum(dim=(2,3)) + 1e-6
        iou = (inter/union).mean().item()
        f1  = (2*inter / (p.sum(dim=(2,3)) + y.sum(dim=(2,3)) + 1e-6)).mean().item()
        return iou, f1

# --- (optional) resume the wider model checkpoint ---
CKPT_PATH = Path("/content/drive/MyDrive/oil_spill_checkpoints/unet_sar2ch_b48.pth")
if CKPT_PATH.exists():
    ckpt = torch.load(CKPT_PATH, map_location=device)
    model.load_state_dict(ckpt["model"])
    print("Loaded b48 checkpoint:", CKPT_PATH, "epoch:", ckpt.get("epoch"), "val_loss:", ckpt.get("val_loss"))
else:
    print("No b48 checkpoint found; you can train fresh.") """