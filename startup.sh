#!/bin/bash

# Make the startup.sh script executable (just in case it's not already)
chmod +x startup.sh

# Install dependencies (make sure you have a requirements.txt in the same directory)
pip install -r requirements.txt

# Start the app using Gunicorn
exec gunicorn -b 0.0.0.0:8000 app:app
