# Installation instructions
1. On laptop: *robot_gui.py*
```
uv add dearpygi
uv add paramiko
```
2. On Raspberry Pi: *robot_control.py*
```
# Make sure SSH is enabled
sudo systemctl enable ssh
sudo systemctl start ssh

# Place robot_control.py in /home/doug/
chmod +x /home/doug/robot_control.py
```
3. Optional - Create a systemd service for your robot:
```
# Create /etc/systemd/system/robot.service
sudo nano /etc/systemd/system/robot.service
```
* Add:
```
[Unit]
Description=Robot Control Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi
ExecStart=/usr/bin/python3 /home/pi/robot_control.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
* Then:
```
sudo systemctl daemon-reload
sudo systemctl enable robot.service
sudo systemctl start robot.service
```
