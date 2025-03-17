import git
import os
import hashlib
import time
import sys
import paho.mqtt.client as mqtt
import ssl
import logging
import smtplib
from email.mime.text import MIMEText
import shutil
import ast
import json

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SelfUpgradingClient")

# MQTT settings
BROKER = "mosquitto"
PORT = 8883
TOPIC = "sensors/data"
USERNAME = "user"
PASSWORD = "pass"
CA_CERT = "certs/ca.crt"
REPO_URL = "https://github.com/EphemeralEon/mqtt-client.git"
CHECK_INTERVAL = 60
FAILED_UPDATE_FILE = "failed_update.json"

# Email settings
EMAIL_FROM = "miha.primozic1@gmail.com"
EMAIL_TO = "miha.primozic1@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "miha.primozic1@gmail.com"
SMTP_PASS = os.getenv("SMTP_PASS", "APP_PASS_HERE")

def send_email(subject, body, retries=3, delay=5):
    for attempt in range(retries):
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = EMAIL_FROM
            msg["To"] = EMAIL_TO
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            logger.info(f"Email sent: {subject}")
            return
        except smtplib.SMTPAuthenticationError:
            logger.error("Email authentication failed. Check SMTP_USER and SMTP_PASS.")
            break
        except Exception as e:
            logger.error(f"Failed to send email (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    logger.error("All email attempts failed.")

def get_checksum(file_path):
    try:
        with open(file_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return None

def is_valid_python(file_path):
    try:
        with open(file_path, "r") as f:
            ast.parse(f.read())
        logger.info(f"Syntax check passed for {file_path}")
        return True
    except SyntaxError as e:
        logger.error(f"Invalid Python syntax in {file_path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error checking {file_path}: {e}")
        return False

def load_failed_update():
    try:
        if os.path.exists(FAILED_UPDATE_FILE):
            with open(FAILED_UPDATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("checksum")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {FAILED_UPDATE_FILE}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading failed update: {e}")
        return None

def save_failed_update(checksum):
    try:
        with open(FAILED_UPDATE_FILE, "w") as f:
            json.dump({"checksum": checksum}, f)
        logger.info(f"Marked update {checksum} as failed")
    except Exception as e:
        logger.error(f"Error saving failed update: {e}")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT broker successfully")
    else:
        logger.error(f"Connection failed with code {rc}")

client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set(ca_certs=CA_CERT, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
client.on_connect = on_connect

# Connect with retry
max_retries = 5
for attempt in range(max_retries):
    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        break
    except Exception as e:
        logger.error(f"Connection attempt {attempt + 1}/{max_retries} failed: {e}")
        if attempt < max_retries - 1:
            time.sleep(5)
        else:
            logger.critical("Max retries reached. Exiting.")
            exit(1)

# Clone or open repo
if not os.path.exists("repo"):
    try:
        git.Repo.clone_from(REPO_URL, "repo")
        logger.info(f"Cloned repo from {REPO_URL}")
    except git.GitCommandError as e:
        logger.critical(f"Failed to clone repo: {e}")
        exit(1)

repo = git.Repo("repo")
current_file = "self_upgrading_client.py"
current_checksum = get_checksum(current_file)
failed_checksum = load_failed_update()

while True:
    try:
        repo.remote().pull()
        new_file = "repo/self_upgrading_client.py"
        if not os.path.exists(new_file):
            logger.error(f"Update file not found in repo: {new_file}")
            time.sleep(CHECK_INTERVAL)
            continue
        
        new_checksum = get_checksum(new_file)
        if new_checksum and current_checksum and new_checksum != current_checksum:
            if new_checksum == failed_checksum:
                logger.info(f"Skipping previously failed update {new_checksum}")
                time.sleep(CHECK_INTERVAL)
                continue
            
            logger.info("Update detected!")
            send_email("Client Update Started", "The client is updating to a new version.")
            
            if not is_valid_python(new_file):
                logger.error("New version has invalid syntax. Aborting update.")
                send_email("Client Update Failed", "New version has invalid syntax. Update aborted.")
                save_failed_update(new_checksum)
                time.sleep(CHECK_INTERVAL)
                continue
            
            # Apply update with backup
            backup = "self_upgrading_client_backup.py"
            if os.path.exists(current_file):
                shutil.copy2(current_file, backup)
            shutil.copy2(new_file, current_file)
            logger.info("Restarting with new version...")
            client.loop_stop()
            os.execv(sys.executable, [sys.executable, current_file])
        else:
            logger.info("No update needed.")
        current_checksum = get_checksum(current_file)
    except git.GitCommandError as e:
        logger.error(f"Git pull failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    time.sleep(CHECK_INTERVAL)
