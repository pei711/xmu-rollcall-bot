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

---

# 厦门大学畅课自动签到机器人

针对厦门大学 TronClass 畅课平台（lnt.xmu.edu.cn）的自动签到脚本，部署在 Linux 服务器上 7×24 小时运行。 签到核心逻辑参考 [XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot)，本仓库提供 7×24 小时服务器轮询的部署方案。

## 功能特性

- **数字签到**：等待班级满 10 人签到后，自动获取签到码并提交；不满 10 人会无限等待（不达标绝不提前签），若签到从列表消失（老师关闭/过期）则自动放弃，避免死循环阻塞。
- **雷达签到**：两探针点三角定位法——先提交两个探针坐标，读取服务器返回的距离，计算两圆交点得到真实位置后提交。
- **二维码签到**：无法自动处理，通过 Server酱 推送微信提醒，人工扫码。
- **微信通知**：所有签到结果通过 Server酱 推送到微信。
- **多账号**：每个账号运行一份脚本副本，通过环境变量 `XMU_ROLLCALL_CONFIG_DIR` 隔离各自的配置与会话缓存。
- **稳定运行**：systemd 服务 + `Restart=always`；session 缓存复用，过期自动重新登录。

## 部署步骤

1. 安装 Python 3.x，`pip install requests`。
2. 创建配置文件（格式见 `config.example.json`）：

```bash
mkdir -p /root/.xmu_rollcall
# 写入 config.json：{"username":"学号","password":"密码","sendkey":"SCT开头的方糖Key"}
```

3. 配置 systemd 服务（示例见上文英文部分），`systemctl enable --now xmu-signin` 即可。

## 致谢

签到核心逻辑（雷达三角定位、签到码处理等）主要参考 [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot)，本项目主要贡献是围绕它的服务器 7×24 小时轮询签到部署脚本。

## 安全说明

学号、密码、方糖 SendKey 全部保存在 `config.json`（已通过 `.gitignore` 排除出仓库），脚本本体不含任何敏感信息。
