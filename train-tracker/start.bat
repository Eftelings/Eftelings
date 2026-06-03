@echo off
cd /d C:\Users\David-Lennart Sturz\eftelings\train-tracker
python -m uvicorn main:app --port 8000
