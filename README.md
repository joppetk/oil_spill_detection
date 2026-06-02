# Integrated System for Oil Spill Detection and Mitigation Leveraging Sentinel-1 SAR Imagery, Deep Learning, and WebRTC Communication Protocol

<p align="center">
  <img src="images/UI-1.jpg" alt="Demo" width="800"/>
  <br>
  <em>Operator user interface</em>
  
</p>

## ✨ Main Objective  

To support environmental protection efforts by enabling rapid, accurate detection and mitigation of marine oil spills through an integrated, technology‑driven response system.

## Features

- Operator web user interface with area of interest (AOI) selection and interactive map
- SAR scene discovery and download management via ASF API
- SNAP-based preprocessing automation (Apply-Orbit-File, Thermal Noise Removal, GRD Border Noise Removal, Calibration, Speckle Filter, Terrain Correction, Linear to dB)
- Model inference execution and visualization overlays
- Incident creation, lifecycle tracking, and audit logs
- Response planning and UAV tasking controls
- Live video monitoring and telemetry panels via WebRTC

## Demonstration Video
Simulated Oil Spill detection and UAV response - Manila Bay, July 2024: https://youtu.be/RU1CvAIvZrg


### Model Overview
- Trained on Sentinel-1 SAR dataset from Zenodo consisting of oil spills, look alikes and images without oil spills
  
**Model performance on 150 test sets**

- **U‑Net + DenseNet201 backbone**  
  - mIoU: 64.37%  
  - F1‑score: 74.34%  
  - FAR: 0.0099  

- **DeepLabV3 + ResNet101 backbone**  
  - mIoU: 54.86%  
  - F1‑score: 63.80%  
  - FAR: 0.0095


## Trained Weights
  U-Net + Densenet201: https://drive.google.com/file/d/1_Z2-cM2uRNvboqkqdsRbA_vzmZYj1Abj/view?usp=sharing  
  DeepLabV3 + Resnet101: https://drive.google.com/file/d/1luY14yLrEWbbwaf63zpVtDmz3BzcuCnu/view?usp=sharing  
  **Download the trained weights and save to the location python-poller/weights**
  
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
│           └── GraphSubset2.xml
│       ├── weights/
│           ├── save deeplab model here
│           ├── save unet model here
│           ├── infer_config_deeplab.json
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
│   ├── drone_ui.desktop     # Autostart
│   ├── main_ui.py
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
  Download and install: https://download.esa.int/step/snap/12.0/installers/esa-snap_all_windows-12.0.0.exe

- **WSL (if Windows)**  
  Install WSL with Ubuntu distro (recommended)

### Required Credentials

1. Create an account at the NASA Earthdata portal:  
   https://urs.earthdata.nasa.gov/home

2. Generate an Earthdata Login (EDL) token from your account settings.

3. Use this token as your ASF Search credential.  
   Paste the generated token into the `EDL_TOKEN` variable inside `scan_download.py`.


### Server Installation (Windows)

1. **Clone the repository**
   ```bash
   git clone https://github.com/joppetk/oil_spill_detection.git
   cd oil_spill_detection

2. **Download the trained weights and save to the location python-poller/weights**

3. **Create virtual environment (choose one)**
   

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

3. **Navigate where server.js is located and run npm install**

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


   

### Incident Management Server Installation (WSL)  

1. **Open your WSL Ubuntu terminal.**
   
2. **Update Ubuntu**

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv libpq-dev python3-dev -y
```

3. **System Preparation.**
   
```bash
cd /mnt/<path-to-your-oil_spill_db_folder>
python3 -m venv venv
```

4. **Database Setup (PostgreSQL + PostGIS)**

```bash
# Install Postgres and PostGIS
sudo apt install -y postgresql postgresql-contrib postgis

# Start the service
sudo service postgresql start

# Create the database user and schema
sudo -u postgres psql -c "CREATE USER oil WITH PASSWORD 'oilpass' SUPERUSER;"
sudo -u postgres psql -c "CREATE DATABASE oil_db OWNER oil;"

# Enable spatial extensions
sudo -u postgres psql -d oil_db -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

5. **Environment Setup**
   
```bash
# Navigate to project folder
cd /path/to/oil_spill_db

# Create virtual environment
python3 -m venv venv

# Activate the environment
source venv/bin/activate
```

6. **Install dependencies**
   
```bash
pip install --upgrade pip
pip install -r requirements-db.txt
```

7. Restore Database Schema

```bash
psql "postgresql://oil:oilpass@127.0.0.1:5432/oil_db" -f database_schema.sql
```

9. **Configuration**
   
```bash
echo 'DATABASE_URL="postgresql+psycopg2://oil:oilpass@127.0.0.1:5432/oil_db"' > .env
```

8. **Run the Server**
   
```bash
python3 api.py
```

  
  
### Raspberry Pi Companion Computer Installation  



### PX4 Simulator Preparation (WSL)

1. **PX4 Setup**
```bash
# Update Ubuntu first
sudo apt update && sudo apt upgrade -y

# Download the PX4 source code
git clone https://github.com/PX4/PX4-Autopilot.git --recursive

# Run the setup script (This takes about 10-15 mins)
bash PX4-Autopilot/Tools/setup/ubuntu.sh
```

2. **Run PX4 Simulator**
```bash
cd ~/PX4-Autopilot
export PX4_HOME_LAT=14.38327
export PX4_HOME_LON=120.57425
export PX4_HOME_ALT=10
Note: Change the coordinates as per your simulator home preference

make px4_sitl none
mavlink stop-all
mavlink start -x -m onboard -u <UDP input port> -o <UDP output port> -t <Raspberry Pi IP address> -r 4000000
or
mavlink start -x -u <UDP input port> -o <UDP output port> -t <Raspberry Pi IP address> -r 4000000
Note: Usually, the UDP input port is 14580 and the UDP output port is 14540.

```
**For Multiple Gazebo worlds**
1. **One-time setup**
```bash
cd ~/PX4-Autopilot
make px4_sitl

mkdir -p ~/bin
wget -O ~/bin/simulation-gazebo https://raw.githubusercontent.com/PX4/PX4-gazebo-models/main/simulation-gazebo
chmod +x ~/bin/simulation-gazebo
```  

2. **World A - Terminal 1: start Gazebo world A**

```bash
python3 ~/bin/simulation-gazebo --world default --gz_partition world_a
```  

4. **Terminal 2: start PX4 for world A**
```bash
cd ~/PX4-Autopilot
GZ_PARTITION=world_a \
PX4_GZ_STANDALONE=1 \
PX4_SYS_AUTOSTART=4001 \
PX4_SIM_MODEL=gz_x500 \
PX4_GZ_WORLD=default \
PX4_HOME_LAT=24.4843 \
PX4_HOME_LON=54.3165 \
PX4_HOME_ALT=10 \
./build/px4_sitl_default/bin/px4 -i 1


mavlink start -x -m onboard -u <UDP input port 1> -o <UDP output port 1> -t <Raspberry Pi 1 IP address> -r 4000000
```

5. **World B - Terminal 3: start Gazebo world B**
```bash
python3 ~/bin/simulation-gazebo --world default --gz_partition world_b
```

6. **Terminal 4: start PX4 for world B**
```bash
cd ~/PX4-Autopilot
GZ_PARTITION=world_b \
PX4_GZ_STANDALONE=1 \
PX4_SYS_AUTOSTART=4001 \
PX4_SIM_MODEL=gz_x500 \
PX4_GZ_WORLD=default \
PX4_HOME_LAT=24.5000 \
PX4_HOME_LON=54.3500 \
PX4_HOME_ALT=10 \
./build/px4_sitl_default/bin/px4 -i 2


mavlink start -x -m onboard -u <UDP input port 2> -o <UDP output port 2> -t <Raspberry Pi 2 IP address> -r 4000000
```


### Raspberry Pi Companion Computer Setup

1. **Remote to Raspberry (Putty-SSH, TigerVNC, VNC Viewer)**

2. **Clone the companion computer**
```bash
git clone --filter=blob:none --sparse https://github.com/joppetk/oil_spill_detection.git
cd oil_spill_detection
git sparse-checkout init --no-cone
git sparse-checkout set companion_computer
git checkout main
```

3. **Install System-Level Dependencies**
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv liblgpio-dev
```

4. **Initialize Virtual Environment & Requirements**
```bash
chmod +x companion_computer/setup.sh
./companion_computer/setup.sh
```

5. **Autostart on Raspberry Pi Desktop Boot**
```bash
mkdir /home/pi/.config/autostart
cp companion_computer/drone_ui.desktop /home/pi/.config/autostart/drone_ui.desktop
chmod +x /home/pi/oil_spill_detection/companion_computer/main_ui.py
```

6. **Allow the insecure origin for the camera to display**  
Navigate to: chrome://flags/#unsafely-treat-insecure-origin-as-secure  
Hit Enable


6. **Reboot and it will launch automatically.**
Default login:
   Username: admin
   Password: admin


7. **Put in Drone/Simulator settings and start mission**




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
