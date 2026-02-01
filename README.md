# Integrated System for Oil Spill Detection and Mitigation Leveraging Sentinel-1 SAR Imagery, Deep Learning, and WebRTC Communication Protocol

<p align="center">
  <img src="https://<your-image-link-or-relative-path>/demo.gif" alt="Demo" width="800"/>
  <br>
  <em>Example: Detected oil spill segmentation overlay on Sentinel-1 SAR image</em>
</p>

State brief objectives here>.

This can help environmental agencies, coast guards, and cleanup teams respond faster to marine pollution events.

## ✨ Features

- Semantic segmentation / object detection / binary classification of oil spills
- Trained on <Sentinel-1 SAR / custom dataset / public dataset like ...>
- <State-of-the-art> performance: mIoU = <xx.x>% / F1-score = <xx.x>% on test set
- Inference in <real-time / <X> seconds per image> on <GPU / CPU>
- Pre-processing pipeline for SAR imagery (speckle filtering, normalization, etc.)
- Incident management
- Integrated drone response (simulated)

## Project Structure

```text
oil-spill-detection/
├── oil_spill_detection/                    
│   ├── server/
│   └── client/
│   └── python-poller/
├── oil_spill_db/
├── companion_computer/
├── notebooks/               # Model training experiments
│   └── deepLabV3+resnet101.ipynb
│   └── U-Net+Densenet201.ipynb
├── README.md
├── demo.ipynb               # Quick demo / visualization

