"""
CampusGuard — Twilio Setup Wizard (Phone Calls + WhatsApp)
Run once: python twilio_setup.py

FREE TRIAL INSTRUCTIONS:
1. Go to https://www.twilio.com/try-twilio (no credit card needed)
2. Get your Account SID and Auth Token from twilio.com/console
3. Get a free Twilio phone number
4. CRITICAL: Go to twilio.com/console/phone-numbers/verified and add the 
   phone numbers you want to call/text (required for free trial accounts).
5. For WhatsApp sandbox: send "join <your-sandbox-word>" to +14155238886
"""

import json
import os
import re
import sys

# Terminal Colors
G = '\033[92m'   # Green
R = '\033[91m'   # Red
Y = '\033[93m'   # Yellow
B = '\033[94m'   # Blue
W = '\033[0m'    # Reset
BOLD = '\033[1m'

# ── Helper Functions ──────────────────────────────────────────────────────────

def check_dependencies():
    """Ensures the Twilio library is installed before proceeding."""
    try:
        import twilio
    except ImportError:
        print(f"\n{R}❌ ERROR: The 'twilio' library is not installed.{W}")
        print(f"{Y}   Please install it by running: pip install twilio{W}\n")
        sys.exit(1)

def get_valid_input(prompt: str, required: bool = True) -> str:
    """Gets input and ensures it's not empty if required."""
    while True:
        value = input(prompt).strip()
        if value or not required:
            return value
        print(f"{R}❌ This field is required. Please try again.{W}")

def get_valid_phone(prompt: str) -> str:
    """Ensures the phone number starts with '+' and contains digits."""
    while True:
        phone = input(prompt).strip()
        if re.match(r'^\+\d{10,15}$', phone):
            return phone
        print(f"{R}❌ Invalid format. Phone numbers must start with '+' and contain 10-15 digits (e.g., +1234567890).{W}")

def save_config(config: dict) -> bool:
    """Safely saves the configuration to JSON."""
    try:
        os.makedirs('data', exist_ok=True)
        with open('data/twilio_config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"\n{R}❌ Failed to save configuration: {e}{W}")
        return False

# ── Main Setup Wizard ─────────────────────────────────────────────────────────

def main():
    check_dependencies()
    
    print(f"\n{BOLD}{B}{'='*65}")
    print(f"  🛡️  CampusGuard — Twilio Setup Wizard")
    print(f"{'='*65}{W}\n")
    print(f"{Y}⚠️  NOTE: If using a Twilio Free Trial, you MUST verify all destination")
    print(f"    phone numbers at: twilio.com/console/phone-numbers/verified{W}\n")

    print(f"{BOLD}--- 1. Twilio Account Credentials ---{W}")
    account_sid = get_valid_input("Twilio Account SID  : ")
    auth_token = get_valid_input("Twilio Auth Token   : ")
    twilio_phone = get_valid_phone("Twilio Phone Number (e.g., +1234567890) : ")
    twilio_wa = get_valid_phone("Twilio WhatsApp Sandbox Number (e.g., +14155238886) : ")
    
    print(f"\n{BOLD}--- 2. Campus Details ---{W}")
    campus = get_valid_input("Campus Name : ")
    public_url = get_valid_input("Public URL for screenshots (leave blank if local only) : ", required=False)

    print(f"\n{BOLD}--- 3. Escalation Contacts ---{W}")
    print(f"{Y}(These are the people who will receive calls/texts during an emergency){W}")
    
    principal_name = get_valid_input("Principal Name  : ")
    principal_phone = get_valid_phone("Principal Phone (e.g., +919876543210) : ")
    
    backup1_name = get_valid_input("Backup 1 Name   : ")
    backup1_phone = get_valid_phone("Backup 1 Phone  : ")
    
    backup2_name = get_valid_input("Backup 2 Name   : ")
    backup2_phone = get_valid_phone("Backup 2 Phone  : ")

    # ── Save Configuration First ──────────────────────────────────────────────
    config = {
        'account_sid': account_sid,
        'auth_token': auth_token,
        'twilio_phone': twilio_phone,
        'twilio_whatsapp_number': twilio_wa,
        'campus_name': campus,
        'public_url': public_url,
        'principal_name': principal_name,
        'principal_phone': principal_phone,
        'backup1_name': backup1_name,
        'backup1_phone': backup1_phone,
        'backup2_name': backup2_name,
        'backup2_phone': backup2_phone,
        'enabled': True,
        'alert_on_high': False  # Only call for CRITICAL by default to avoid spam
    }

    if not save_config(config):
        sys.exit(1)

    # ── Test Call ─────────────────────────────────────────────────────────────
    print(f"\n{B}🔄 Sending test call to {principal_name}...{W}")
    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)
        
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="en-IN">
    Hello. This is a test call from Campus Guard.
    Your emergency alert system is now configured and working correctly.
    Thank you.
  </Say>
</Response>"""
        
        call = client.calls.create(
            twiml=twiml,
            to=principal_phone,
            from_=twilio_phone
        )
        print(f"{G}✅ Test call initiated successfully!{W}")
        print(f"   Call SID: {call.sid}")
        
    except Exception as e:
        error_msg = str(e).lower()
        print(f"\n{R}⚠️  Test call failed.{W}")
        
        # Provide actionable advice based on common Twilio trial errors
        if 'unverified destination' in error_msg or 'trial account' in error_msg:
            print(f"{Y}   REASON: You are using a Twilio Free Trial.{W}")
            print(f"{Y}   FIX: Go to twilio.com/console/phone-numbers/verified")
            print(f"        and add '{principal_phone}' as a Verified Caller ID.{W}")
        elif 'invalid phone number' in error_msg:
            print(f"{Y}   REASON: The phone number format is invalid or unsupported.{W}")
        else:
            print(f"{Y}   REASON: {str(e)}{W}")
            
        print(f"\n{B}💡 Don't worry! Your configuration has been saved.{W}")
        print(f"   You can test it again later from the Dashboard once verified.\n")

    # ── Success Summary ───────────────────────────────────────────────────────
    print(f"\n{G}{'='*65}")
    print(f"  ✅ CONFIGURATION SAVED SUCCESSFULLY!")
    print(f"{'='*65}{W}\n")
    
    print(f"{BOLD}🚨 ESCALATION FLOW:{W}")
    print(f"  1. Threat detected → 📢 PC Siren (18s) + Dashboard Alert")
    print(f"  2. 30s no response → 📞 CALL + WhatsApp to {principal_name}")
    print(f"  3. 30s no answer   → 📞 CALL + WhatsApp to {backup1_name}")
    print(f"  4. 30s no answer   → 📞 CALL + WhatsApp to {backup2_name}\n")
    
    print(f"{Y}📌 NEXT STEPS:{W}")
    print(f"  1. If using a Free Trial, verify all phone numbers in Twilio Console.")
    print(f"  2. For WhatsApp, ensure you've joined the sandbox via WhatsApp.")
    print(f"  3. Start the main application: {G}python app.py{W}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Y}⚠️  Setup cancelled by user.{W}\n")
        sys.exit(0)