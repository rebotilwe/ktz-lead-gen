@echo off
REM Double-click this file to launch the KTZ Lead Engine in your browser.
cd /d "%~dp0"

if not exist venv (
    echo First-time setup: creating a virtual environment and installing dependencies...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

streamlit run app.py
