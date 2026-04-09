#!/usr/bin/env bash
set -e

python3 -m venv --system-site-packages drone-venv
source drone-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r companion_computer/requirements.txt
