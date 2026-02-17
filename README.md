# Integrated System for Oil Spill Detection and Mitigation Leveraging Sentinel-1 SAR Imagery, Deep Learning, and WebRTC Communication Protocol

<p align="center">
  <img src="images/UI-1.jpg" alt="Demo" width="800"/>
  <br>
  <em>Operator user interface</em>
  `<em>Example: Detected oil spill segmentation overlay on Sentinel-1 SAR image</em>`
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
- Model performance on 150 test sets:  
    U-Net + Densenet201 backbone: mIoU = 78.82% / F1-score = 87.15% / FAR = 0.0091  
    DeepLabV3 + Resnet101 backbone: mIoU = 72.00% / F1-score = 80.88% / FAR = 0.0110  


## Trained Weights
  U-Net + Densenet201: https://drive.google.com/file/d/1ktmrBPq3buC-MpcDYv15L3GoCqkNHRzh/view?usp=drive_link  
  DeepLabV3 + Resnet101: https://drive.google.com/file/d/1rSZWQ5doakFCwIP0Z2kunUzwfogrA12-/view?usp=drive_link
  
## Project Structure

```text
oil-spill-detection/
├── oil_spill_detection/                    
│   ├── server/
│       ├── server.js
│       ├── scheduler.js
│       ├── rtc.js
│       └── package.json
│   └── client/
│       ├── operator.html
│       ├── operator.js
│       ├── pi.html
│       └── pi.js
│   └── python-poller/
│       ├── data/
│       ├── snap-graphs/
│           ├── best_model.pt
│           └── GraphSubset2.xml
│       ├── weights/
│           └── infer_config.json
│       ├── infer_latest.py
│       ├── infer_utils.py
│       ├── run_gpt_once.py
│       └── scan_download.py
│   └── certs/      
│       ├── cert.key
│       └── cert.pem
├── oil_spill_db/
│   ├── ne_10m_coastline/
│   ├── api.py
│   ├── db.py
│   ├── ims_ops.py
│   └── sensitive_areas.py
├── companion_computer/
│   ├── px4_ops.py
│   └── service2.py
├── notebooks/               # Model training experiments
│   ├── deepLabV3+resnet101.ipynb
│   └── U-Net+Densenet201.ipynb
├── README.md
├── requirements.txt
├── demo.ipynb               # Quick demo / visualization
```

## Getting Started

### Prerequisites

- Python ≥ 3.9
- GPU recommended (NVIDIA with ≥8 GB VRAM for training)

### Required Software

- **ESA SNAP 12.0.0**  
  Download: https://download.esa.int/step/snap/12.0/installers/esa-snap_all_windows-12.0.0.exe

### Required Credentials

1. Create an account at the NASA Earthdata portal:  
   https://urs.earthdata.nasa.gov/home

2. Generate an Earthdata Login (EDL) token from your account settings.

3. Use this token as your ASF Search credential.  
   Paste the generated token into the `EDL_TOKEN` variable inside `scan_download.py`.


### Installation (Windows)

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
conda create --name oil-spill python=3.9
conda activate oil-spill
```

3. **Navigate the where server.js is located and run npm install**

 ```bash
npm install

```

4. **Install python libraries on the same environment**

```bash
pip install -r requirements.txt
```

6. **Run the server**

 ```bash

nodemon .\server.js
```

5. **Open the application in your web browser**

```bash
https://<your-server-ipaddress>:8181/operator.html
```

5. 

## Acknowledgments / References

**Dataset:** R. Trujillo-Acatitla, J. Tuxpan-Vargas, C. Ovando-Vázquezand E. Monterrubio-Martínez, “Sentinel-1 SAR Oil spill image dataset for train, validate, and test deep learning models.

**Links:**
- https://doi.org/10.5281/zenodo.8346860
- https://doi.org/10.5281/zenodo.8253899
- https://doi.org/10.5281/zenodo.13761290


**Software / Tools:**  
This work makes use of the **ESA Sentinel Application Platform (SNAP)**, developed by  
**the European Space Agency (ESA)** and **Brockmann Consult GmbH**.  
Learn more at: https://step.esa.int/main/toolboxes/snap/
``


## Contact

Joppet Karlo Quinones – <joppetk_q@yahoo.com> – feel free to reach out!  
Project Link: https://github.com/joppetk/oil_spill_detection
