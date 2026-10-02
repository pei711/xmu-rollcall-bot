# XMU Rollcall Auto Sign-in Bot

Automated roll-call answering bot for Xiamen University TronClass platform (lnt.xmu.edu.cn), running 24/7 on a Linux server. The answering logic is based on [XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot); this repo provides a 24/7 server polling wrapper.

## Features

- **Number roll-call**: Waits until at least 10 classmates have signed in, then automatically fetches the number code and submits. Keeps waiting (non-stop) until the threshold is met; gives up gracefully if the roll-call disappears from the list.
- **Radar roll-call**: Uses a two-probe trilateration method — submits two probe coordinates, reads the reported distances from the server, computes the intersection of two circles, and answers with the solved position.
- **QR-code roll-call**: Cannot be automated. Sends a ServerChan (WeChat push) reminder so you can scan it manually.
- **WeChat notifications**: Every result is pushed via ServerChan.
- **Multi-account**: Run one copy of the script per account, each with its own config directory (set via `XMU_ROLLCALL_CONFIG_DIR`).
- **Survivability**: Runs as a systemd service with `Restart=always`; cached sessions are reused and re-login happens automatically on expiry.

## Deployment

1. Install Python 3.x and `pip install requests`.
2. Create a config file:

```bash
mkdir -p /root/.xmu_rollcall
cat > /root/.xmu_rollcall/config.json << 'EOF'
{
  "username": "your_student_id",
  "password": "your_password",
  "sendkey": "your_ServerChan_SendKey"
}
EOF
```

3. Install as a systemd service (`/etc/systemd/system/xmu-signin.service`):

```ini
[Unit]
Description=XMU Rollcall Auto Sign-in Service
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /root/xmu_signin.py
Restart=always
[Install]
WantedBy=multi-user.target
```

4. `systemctl enable --now xmu-signin`

## Acknowledgement

The core roll-call answering logic (radar trilateration, number-code handling, etc.) is largely based on [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot). This project mainly contributes a server-side 24/7 polling deployment script around it.

## Security

All credentials (student ID, password, ServerChan key) live in `config.json`, which is excluded from this repository via `.gitignore`. The script itself contains no secrets.
