FROM python:3.7

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rc_channel_sync.py .
ENTRYPOINT ["python3", "rc_channel_sync.py"]
