# Integrated System for Oil Spill Detection and Mitigation Leveraging Sentinel-1 SAR Imagery, Deep Learning, and WebRTC Communication Protocol

<p align="center">
  <img src="https://<your-image-link-or-relative-path>/demo.gif" alt="Demo" width="800"/>
  <br>
  <em>Example: Detected oil spill segmentation overlay on Sentinel-1 SAR image</em>
</p>

State brief objectives here>.

This can help environmental agencies, coast guards, and cleanup teams respond faster to marine pollution events.

## ✨ Features

- Operator web user interface with area of interest (AOI) selection and interactive map
- SAR scene discovery and download management via ASF API
- SNAP-based preprocessing automation (Apply-Orbit-File, Thermal Noise Removal, GRD Border Noise Removal, Calibration, Speckle Filter, Terrain Correction, Linear to dB)
- Model inference execution and visualization overlays
- Incident creation, lifecycle tracking, and audit logs
- Response planning and UAV tasking controls
- Live video monitoring and telemetry panels via WebRTC

### Model Overview
- Trained on Sentinel-1 SAR dataset from Zenodo consisting of oil spills, look alikes and images without oil spills
- Model performance: mIoU = 71.13% / F1-score = 80.42% on test set
- Inference in <real-time / <X> seconds per image> on <GPU / CPU>


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
```

## Getting Started

### Prerequisites

- Python ≥ 3.9
- GPU recommended (NVIDIA with ≥8 GB VRAM for training)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/joppetk/oil_spill_detection.git
   cd oil_spill_detection

2. **Create virtual environment (choose one)**

Using venv (recommended)  
 ```bash
python -m venv venv
source venv/bin/activate    # Linux / macOS
venv\Scripts\activate       # Windows
```

Or using conda  
 ```bash
conda env create -f environment.yml
conda activate oil-spill
```

## Acknowledgments / References

**Dataset:** R. Trujillo-Acatitla, J. Tuxpan-Vargas, C. Ovando-Vázquezand E. Monterrubio-Martínez, “Sentinel-1 SAR Oil spill image dataset for train, validate, and test deep learning models.

**Links:**
- https://doi.org/10.5281/zenodo.8346860
- https://doi.org/10.5281/zenodo.8253899
- https://doi.org/10.5281/zenodo.13761290

## Contact

Joppet Karlo Quinones – <joppetk_q@yahoo.com> – feel free to reach out!  
Project Link: https://github.com/joppetk/oil_spill_detection
