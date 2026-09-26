"""
CampusGuard — Alert Manager v4
Multi-layer escalating alert system with thread-safe operations and cross-platform support.

CRITICAL threat detected
    ↓
PC Siren (18 sec) + Dashboard red alert
    ↓
30 sec — guard no response
    ↓
Twilio CALLS Principal + WhatsApp + Gmail
    ↓
30 sec — no answer
    ↓
Twilio CALLS Backup 1 + WhatsApp
    ↓
30 sec — no answer
    ↓
Twilio CALLS Backup 2 + WhatsApp
"""

import smtplib
import json
import os
import time
import threading
import logging
import platform
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
from typing import Dict, Any, Optional

# Configure professional logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("CampusGuard.AlertManager")

# Thread lock for file operations to prevent race conditions
file_lock = threading.Lock()

EMAIL_CONFIG  = 'data/email_config.json'
TWILIO_CONFIG = 'data/twilio_config.json'
ACK_FILE      = 'data/acknowledgements.json'
UNACK_FILE    = 'data/unacknowledged.json'
CALL_LOG_FILE = 'data/call_log.json'

SEVERITY_COLORS = {
    'CRITICAL': '#dc2626',
    'HIGH':     '#ea580c',
    'MEDIUM':   '#d97706',
}

class AlertManager:
    def __init__(self, escalation_wait: int = 30):
        self.escalation_wait = escalation_wait
        self.escalations: Dict[str, Dict[str, Any]] = {}
        self.siren_on = False
        self._siren_thread: Optional[threading.Thread] = None
        
        # Initial load
        self.email_cfg = self._load_json(EMAIL_CONFIG, {})
        self.twilio_cfg = self._load_json(TWILIO_CONFIG, {})

    def _load_json(self, path: str, default: Any) -> Any:
        with file_lock:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"Failed to load {path}: {e}")
            return default

    def _save_json(self, path: str, data: Any) -> None:
        with file_lock:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to save {path}: {e}")

    def _log_event(self, path: str, entry: Dict[str, Any]) -> None:
        with file_lock:
            try:
                log_data = []
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        try:
                            log_data = json.load(f)
                        except json.JSONDecodeError:
                            log_data = []
                
                # Keep log size manageable (max 500 entries)
                log_data.insert(0, entry)
                if len(log_data) > 500:
                    log_data = log_data[:500]
                    
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(log_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to log event to {path}: {e}")

    # ── Main entry ────────────────────────────────────────────────────────────

    def send_alert(self, alert: Dict[str, Any], screenshot_filename: Optional[str] = None) -> None:
        severity = alert.get('severity', 'MEDIUM')
        alert_type = alert.get('type', 'ALERT')
        alert_id = f"{alert_type}_{int(time.time())}"

        logger.info(f"Processing {severity} alert: {alert_id}")

        # 1. Email for CRITICAL + HIGH
        if self.email_cfg.get('enabled') and severity in ('CRITICAL', 'HIGH'):
            threading.Thread(
                target=self._send_email,
                args=(alert, screenshot_filename),
                daemon=True,
                name=f"EmailThread-{alert_id}"
            ).start()

        # 2. CRITICAL — siren + escalation chain
        if severity == 'CRITICAL':
            self._play_siren(18)
            self.escalations[alert_id] = {'acknowledged': False}
            threading.Thread(
                target=self._escalation_chain,
                args=(alert_id, alert, screenshot_filename),
                daemon=True,
                name=f"EscalationThread-{alert_id}"
            ).start()

        # 3. HIGH — WhatsApp to principal only, no call
        elif severity == 'HIGH' and self.twilio_cfg.get('enabled'):
            phone = self.twilio_cfg.get('principal_phone')
            name = self.twilio_cfg.get('principal_name', 'Principal')
            if phone:
                threading.Thread(
                    target=self._send_whatsapp,
                    args=(alert, screenshot_filename, phone, name),
                    daemon=True,
                    name=f"WhatsAppThread-{alert_id}"
                ).start()

    # ── Acknowledge ───────────────────────────────────────────────────────────

    def acknowledge(self, alert_id: str, by: str = 'guard') -> bool:
        if alert_id in self.escalations:
            self.escalations[alert_id]['acknowledged'] = True
            self._stop_siren()
            self._log_event(ACK_FILE, {
                'alert_id': alert_id, 
                'by': by,
                'at': datetime.now().isoformat()
            })
            logger.info(f"✅ Alert {alert_id} acknowledged by {by}")
            return True
        
        logger.warning(f"Attempted to acknowledge unknown alert: {alert_id}")
        return False

    # ── Escalation chain ──────────────────────────────────────────────────────

    def _escalation_chain(self, alert_id: str, alert: Dict[str, Any], screenshot: Optional[str]) -> None:
        cfg = self.twilio_cfg
        recipients = [
            (cfg.get('principal_name', 'Principal'), cfg.get('principal_phone')),
            (cfg.get('backup1_name', 'Backup 1'), cfg.get('backup1_phone')),
            (cfg.get('backup2_name', 'Backup 2'), cfg.get('backup2_phone')),
        ]
        
        for name, phone in recipients:
            if not phone:
                continue
                
            logger.info(f"⏳ Waiting {self.escalation_wait}s before escalating to {name}...")
            
            # Wait with interruption check
            for _ in range(self.escalation_wait):
                time.sleep(1)
                if self.escalations.get(alert_id, {}).get('acknowledged'):
                    logger.info(f"✅ Alert {alert_id} acknowledged. Stopping escalation to {name}.")
                    return
                    
            logger.warning(f"🚨 Escalating to {name} ({phone})")
            
            # Trigger call and whatsapp in parallel
            threading.Thread(target=self._make_call, args=(alert, phone, name), daemon=True).start()
            threading.Thread(target=self._send_whatsapp, args=(alert, screenshot, phone, name), daemon=True).start()

        logger.critical(f"🚨 All escalation levels notified for {alert_id}")
        self._log_event(UNACK_FILE, {
            'alert_id': alert_id, 
            'type': alert.get('type'),
            'at': datetime.now().isoformat()
        })

    # ── Twilio Phone Call ─────────────────────────────────────────────────────

    def _make_call(self, alert: Dict[str, Any], to_phone: str, recipient_name: str) -> None:
        cfg = self.twilio_cfg
        if not cfg.get('account_sid') or not cfg.get('auth_token'):
            logger.warning("⚠️ Twilio not configured — skipping call")
            return
            
        alert_type = alert.get('type', 'THREAT').replace('_', ' ')
        zone = alert.get('zone', 'campus premises')
        campus = cfg.get('campus_name', 'the campus')
        
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="en-IN">
    Emergency alert. {alert_type} detected. Immediate action required.
  </Say>
</Response>"""
        try:
            from twilio.rest import Client
            client = Client(cfg['account_sid'], cfg['auth_token'])
            call = client.calls.create(
                twiml=twiml, 
                to=to_phone, 
                from_=cfg['twilio_phone']
            )
            logger.info(f"✅ Call to {recipient_name} initiated — SID: {call.sid}")
            self._log_event(CALL_LOG_FILE, {
                'to': recipient_name, 
                'phone': to_phone,
                'alert': alert.get('type'), 
                'at': datetime.now().isoformat(), 
                'sid': call.sid
            })
        except ImportError:
            logger.error("⚠️ Twilio library not installed. Run: pip install twilio")
        except Exception as e:
            logger.error(f"⚠️ Call error to {recipient_name}: {e}")

    # ── Twilio WhatsApp ───────────────────────────────────────────────────────

    def _send_whatsapp(self, alert: Dict[str, Any], screenshot_filename: Optional[str],
                        to_phone: str, recipient_name: str) -> None:
        cfg = self.twilio_cfg
        if not cfg.get('account_sid') or not cfg.get('auth_token'):
            return
            
        alert_type = alert.get('type', 'THREAT').replace('_', ' ')
        message = alert.get('message', 'Threat detected')
        zone = alert.get('zone', 'Unknown Zone')
        severity = alert.get('severity', 'CRITICAL')
        ts = datetime.now().strftime('%d %b %Y %I:%M:%S %p')
        campus = cfg.get('campus_name', 'Campus')
        
        body = (
            f"🚨 *{severity} ALERT — CampusGuard*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{message}\n\n"
            f"📍 *Zone*    : {zone}\n"
            f"🏫 *Campus* : {campus}\n"
            f"🕐 *Time*    : {ts}\n"
            f"👤 *Alert to*: {recipient_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Immediate action required."
        )
        try:
            from twilio.rest import Client
            client = Client(cfg['account_sid'], cfg['auth_token'])
            
            # Send text message
            client.messages.create(
                body=body,
                from_=f"whatsapp:{cfg['twilio_whatsapp_number']}",
                to=f"whatsapp:{to_phone}"
            )
            logger.info(f"✅ WhatsApp text sent to {recipient_name}")
            
            # Send image if available and public URL is configured
            if screenshot_filename and cfg.get('public_url'):
                img_url = f"{cfg['public_url']}/static/screenshots/{screenshot_filename}"
                client.messages.create(
                    body="📸 Screenshot evidence:",
                    media_url=[img_url],
                    from_=f"whatsapp:{cfg['twilio_whatsapp_number']}",
                    to=f"whatsapp:{to_phone}"
                )
                logger.info(f"✅ WhatsApp image sent to {recipient_name}")
                
        except ImportError:
            logger.error("⚠️ Twilio library not installed. Run: pip install twilio")
        except Exception as e:
            logger.error(f"⚠️ WhatsApp error to {recipient_name}: {e}")

    # ── PC Siren (Cross-Platform) ─────────────────────────────────────────────

    def _play_siren(self, duration: int = 18) -> None:
        if self.siren_on:
            return
            
        self.siren_on = True
        logger.warning(f"🔊 PC Siren activated for {duration} seconds")
        
        def siren_loop():
            end_time = time.time() + duration
            system = platform.system()
            
            while time.time() < end_time and self.siren_on:
                try:
                    if system == 'Windows':
                        import winsound
                        winsound.Beep(1200, 400)
                        if not self.siren_on: break
                        winsound.Beep(800, 400)
                    elif system == 'Darwin':  # macOS
                        subprocess.call(['afplay', '/System/Library/Sounds/Ping.aiff'])
                        time.sleep(0.5)
                    elif system == 'Linux':
                        subprocess.call(['paplay', '/usr/share/sounds/alsa/Front_Center.wav'])
                        time.sleep(0.5)
                    else:
                        logger.info("🔔 [SIREN SIMULATION] BEEP BEEP")
                        time.sleep(1)
                except Exception as e:
                    logger.debug(f"Siren playback detail: {e}")
                    time.sleep(1) # Fallback wait
                    
            self.siren_on = False
            logger.info("🔇 PC Siren stopped")

        self._siren_thread = threading.Thread(target=siren_loop, daemon=True, name="SirenThread")
        self._siren_thread.start()

    def _stop_siren(self) -> None:
        if self.siren_on:
            self.siren_on = False
            logger.info("🔇 Siren stop signal sent")

    # ── Gmail Email ───────────────────────────────────────────────────────────

    def _send_email(self, alert: Dict[str, Any], screenshot_filename: Optional[str] = None) -> None:
        logger.info(f"📧 Attempting to send email to {self.email_cfg.get('recipient_email')}")
        logger.info(f"   Config: {self.email_cfg}")
        cfg = self.email_cfg
        if not cfg.get('sender_email') or not cfg.get('sender_password'):
            logger.warning("⚠️ Email credentials not configured — skipping email")
            return
            
        alert_type = alert.get('type', 'ALERT').replace('_', ' ')
        severity = alert.get('severity', 'HIGH')
        zone = alert.get('zone', 'Unknown')
        ts = datetime.now().strftime('%d %b %Y %I:%M:%S %p')
        color = SEVERITY_COLORS.get(severity, '#dc2626')
        campus = cfg.get('campus_name', 'Campus')
        message = alert.get('message', 'Threat detected')
        
        msg = MIMEMultipart('alternative')
        msg['From'] = cfg['sender_email']
        msg['To'] = cfg.get('recipient_email', cfg['sender_email'])
        msg['Subject'] = f"🚨 [{severity}] {alert_type} — {campus} | {ts}"
        
        html = f"""
        <html><body style="font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; padding: 20px; margin: 0;">
        <div style="max-width: 600px; margin: 0 auto; background: #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.3);">
          <div style="background: {color}; padding: 24px; text-align: center;">
            <h1 style="color: #fff; margin: 0; font-size: 24px; font-weight: 700;">🚨 {severity} ALERT</h1>
            <p style="color: #fff; margin: 8px 0 0; opacity: 0.9; font-size: 14px;">{campus} — CampusGuard Security System</p>
          </div>
          <div style="padding: 24px;">
            <p style="color: #e2e8f0; font-size: 16px; margin: 0 0 20px; line-height: 1.5;">{message}</p>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
              <tr><td style="padding: 10px; color: #94a3b8; border-bottom: 1px solid #334155;">Alert Type</td>
                  <td style="padding: 10px; color: #f1f5f9; border-bottom: 1px solid #334155; font-weight: 600;">{alert_type}</td></tr>
              <tr><td style="padding: 10px; color: #94a3b8; border-bottom: 1px solid #334155;">Zone</td>
                  <td style="padding: 10px; color: #f1f5f9; border-bottom: 1px solid #334155; font-weight: 600;">{zone}</td></tr>
              <tr><td style="padding: 10px; color: #94a3b8; border-bottom: 1px solid #334155;">Time</td>
                  <td style="padding: 10px; color: #f1f5f9; border-bottom: 1px solid #334155; font-weight: 600;">{ts}</td></tr>
              <tr><td style="padding: 10px; color: #94a3b8;">Severity</td>
                  <td style="padding: 10px; color: {color}; font-weight: 700;">{severity}</td></tr>
            </table>
            <div style="margin-top: 20px; padding: 16px; background: #0f172a; border-radius: 8px; border-left: 4px solid {color};">
              <p style="margin: 0; color: #94a3b8; font-size: 13px;">📎 Screenshot evidence is attached to this email.<br>
              🔗 <a href="http://localhost:5000" style="color: {color}; text-decoration: none; font-weight: 600;">View Dashboard</a></p>
            </div>
          </div>
        </div></body></html>"""
        
        msg.attach(MIMEText(html, 'html'))
        
        if screenshot_filename:
            path = os.path.join('static', 'screenshots', screenshot_filename)
            if os.path.exists(path):
                try:
                    with open(path, 'rb') as f:
                        img = MIMEImage(f.read(), name=screenshot_filename)
                        img.add_header('Content-Disposition', 'attachment', filename=screenshot_filename)
                        msg.attach(img)
                except Exception as e:
                    logger.error(f"Failed to attach screenshot: {e}")
                    
        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
                s.login(cfg['sender_email'], cfg['sender_password'])
                s.sendmail(cfg['sender_email'], cfg.get('recipient_email', cfg['sender_email']), msg.as_string())
            logger.info(f"✅ Email sent successfully [{severity}]")
        except Exception as e:
            logger.error(f"⚠️ Email sending error: {e}")

    def send_test_alert(self) -> Dict[str, bool]:
        test_alert = {
            'type': 'TEST_ALERT', 
            'message': '🔔 CampusGuard test — system is functioning correctly.',
            'severity': 'HIGH', 
            'zone': 'Test Zone', 
            'timestamp': datetime.now().isoformat()
        }
        self.send_alert(test_alert, None)
        return {'success': True}