FROM python:3.7

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rc_sync.py rc_client.py ldap_client.py ./
ENTRYPOINT ["python3", "rc_sync.py"]
