#!/bin/bash
# Double-click this file to launch the KTZ Lead Engine in your browser.
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "First-time setup: creating a virtual environment and installing dependencies..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

streamlit run app.py
