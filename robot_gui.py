import dearpygui.dearpygui as dpg
import paramiko
import threading
import time
# import json
from collections import deque
import queue
import numpy as np
import os

class RobotSSHGUI:
    def __init__(self, host="raspibot.local", port=22, username="doug", password="robot"):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_key = False
        self.key_path = ""
        
        self.ssh_client = None
        self.connected = False
        self.command_queue = queue.Queue()
        self.response_queue = queue.Queue()
        
        # Robot status
        self.robot_status = {
            "cpu_temp": 0.0,
            "cpu_usage": 0.0,
            "memory_usage": 0.0,
            "disk_usage": 0.0,
            "battery": 0.0,
            "battery_voltage": 0.0,
            "battery_current": 0.0,
            "battery_power": 0.0,
            "position": {"x": 0.0, "y": 0.0, "theta": 0.0},
            "velocity": {"linear": 0.0, "angular": 0.0},
            "status": "Disconnected"
        }

        # Telemetry history
        self.cpu_history = []
        self.memory_history = []
        self.time_points = []
        self.time_counter = 0
        
        # Command log
        self.command_log = deque(maxlen=50)
        
        # SSH threads
        self.status_thread = None
        self.command_thread = None
        self.running = False
        self.ssh_lock = threading.Lock()

        # Camera settings
        self.camera_enabled = False
        self.camera_width = 640
        self.camera_height = 480
        self.camera_thread = None
        
    def setup_ssh_connection(self):
        """Establish SSH connection"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if self.use_key and self.key_path:
                key_path = os.path.expanduser(self.key_path)
                if not os.path.exists(key_path):
                    raise Exception(f"SSH key not found: {key_path}")
                
                try:
                    key = paramiko.RSAKey.from_private_key_file(key_path)
                except:
                    try:
                        key = paramiko.Ed25519Key.from_private_key_file(key_path)
                    except:
                        key = paramiko.ECDSAKey.from_private_key_file(key_path)
                
                self.ssh_client.connect(self.host, port=self.port, 
                                       username=self.username, pkey=key, timeout=10)
            else:
                self.ssh_client.connect(self.host, port=self.port,
                                       username=self.username, password=self.password, timeout=10)
            
            self.connected = True
            self.robot_status["status"] = "Connected"
            self.log_command(f"✓ Connected to {self.username}@{self.host}")
            dpg.set_value("connection_status", "Status: Connected")
            dpg.configure_item("connection_status", color=(0, 255, 0))
            
            # Start monitoring threads
            self.running = True
            self.status_thread = threading.Thread(target=self.monitor_status, daemon=True)
            self.command_thread = threading.Thread(target=self.process_commands, daemon=True)
            self.battery_thread = threading.Thread(target=self.monitor_battery, daemon=True)
            self.status_thread.start()
            self.command_thread.start()
            self.battery_thread.start()
            
            return True
            
        except paramiko.AuthenticationException:
            self.connected = False
            error_msg = "Authentication failed - check username/password"
            self.log_command(f"✗ {error_msg}")
            dpg.set_value("connection_status", f"Status: {error_msg}")
            dpg.configure_item("connection_status", color=(255, 0, 0))
            return False
            
        except Exception as e:
            self.connected = False
            self.log_command(f"✗ Connection failed: {e}")
            dpg.set_value("connection_status", f"Status: Error - {str(e)[:50]}")
            dpg.configure_item("connection_status", color=(255, 0, 0))
            return False
    
    def disconnect_ssh(self):
        """Disconnect SSH connection"""
        self.running = False
        if self.ssh_client:
            self.ssh_client.close()
            self.connected = False
            self.robot_status["status"] = "Disconnected"
            self.log_command("Disconnected from robot")
            dpg.set_value("connection_status", "Status: Disconnected")
            dpg.configure_item("connection_status", color=(255, 0, 0))
    
    def setup_camera_texture(self):
        """Initialize camera texture"""
        
        # Create initial blank texture
        blank_image = np.zeros((self.camera_height, self.camera_width, 3), dtype=np.float32)
        blank_image[:, :] = [0.1, 0.1, 0.1]  # Dark gray
        
        # Flatten and normalize to 0-1 range
        texture_data = blank_image.flatten()
        
        with dpg.texture_registry():
            dpg.add_raw_texture(
                width=self.camera_width,
                height=self.camera_height,
                default_value=texture_data,
                format=dpg.mvFormat_Float_rgb,
                tag="camera_texture"
            )

    def start_camera_stream(self):
        """Start rpicam-vid TCP stream on the robot"""
        try:
            # Kill any existing camera processes
            self.execute_command("pkill rpicam-vid", timeout=2)
            time.sleep(0.5)
            
            # Start rpicam-vid in background with TCP streaming
            cmd = 'nohup rpicam-vid -t 0 --width 640 --height 480 --framerate 15 --codec mjpeg --listen -o tcp://0.0.0.0:8080 > /dev/null 2>&1 &'
            self.execute_command(cmd, timeout=2)
            time.sleep(2)  # Give it time to start
            print("Camera TCP stream started")
        except Exception as e:
            print(f"Failed to start camera stream: {e}")

    def stop_camera_stream(self):
        """Stop camera stream on the robot"""
        try:
            self.execute_command("pkill rpicam-vid", timeout=2)
            print("Camera stream stopped")
        except Exception as e:
            print(f"Failed to stop camera stream: {e}")

    def monitor_camera(self):
        """Background thread to update camera feed from TCP MJPEG stream"""
        from PIL import Image
        from io import BytesIO
        import socket
        
        # Start the stream on the robot
        self.start_camera_stream()
        
        # Connect to TCP stream
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            sock.connect((self.host, 8080))
            print("Connected to camera stream")
        except Exception as e:
            print(f"Failed to connect to camera stream: {e}")
            return
        
        bytes_data = bytes()
        
        while self.running and self.connected and self.camera_enabled:
            try:
                # Read data from socket
                chunk = sock.recv(4096)
                if not chunk:
                    print("Stream ended, reconnecting...")
                    sock.close()
                    time.sleep(1)
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(10)
                    sock.connect((self.host, 8080))
                    bytes_data = bytes()
                    continue
                
                bytes_data += chunk
                
                # Find JPEG boundaries
                a = bytes_data.find(b'\xff\xd8')  # JPEG start
                b = bytes_data.find(b'\xff\xd9')  # JPEG end
                
                if a != -1 and b != -1:
                    jpg = bytes_data[a:b+2]
                    bytes_data = bytes_data[b+2:]
                    
                    # Decode image
                    img = Image.open(BytesIO(jpg))
                    img = img.resize((self.camera_width, self.camera_height))
                    img_array = np.array(img, dtype=np.float32) / 255.0
                    
                    # Update texture
                    texture_data = img_array.flatten()
                    dpg.set_value("camera_texture", texture_data)
                
            except socket.timeout:
                print("Socket timeout, continuing...")
                continue
            except Exception as e:
                print(f"Camera streaming error: {e}")
                time.sleep(1)
        
        sock.close()
        self.stop_camera_stream()

    def toggle_camera(self):
        """Start/stop camera feed"""
        self.camera_enabled = not self.camera_enabled
        
        if self.camera_enabled:
            self.camera_thread = threading.Thread(target=self.monitor_camera, daemon=True)
            self.camera_thread.start()
            dpg.configure_item("camera_toggle_btn", label="Stop Camera")
        else:
            dpg.configure_item("camera_toggle_btn", label="Start Camera")

    def execute_command(self, command, timeout=10, get_pty=False):
        """Execute SSH command and return output"""
        if not self.connected or not self.ssh_client:
            # print(f"Command '{command}' failed: Not connected")
            return None, "Not connected"
        
        with self.ssh_lock:
            try:
                stdin, stdout, stderr = self.ssh_client.exec_command(command, timeout=timeout, get_pty=get_pty)
                
                # Wait for command to complete FIRST
                exit_status = stdout.channel.recv_exit_status()
                
                # Then read all output
                output = stdout.read().decode('utf-8').strip()
                error = stderr.read().decode('utf-8').strip()
                
                return output, error
            except Exception as e:
                print(f"Command execution exception: {e}")
                return None, str(e)

    def monitor_battery(self):
        """Background thread to monitor battery status"""
        time.sleep(3)
        
        while self.running and self.connected:
            try:
                command = "timeout 3 python3 -u ~/UPS/INA219.py"
                output, error = self.execute_command(command, timeout=5, get_pty=True)
                
                if output:
                    # Parse the output
                    lines = output.split('\n')
                    parsed_data = {}
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        if 'Load Voltage:' in line or 'Voltage:' in line:
                            try:
                                voltage_str = line.split(':')[1].strip().split('V')[0].strip()
                                parsed_data['voltage'] = float(voltage_str)
                            except (ValueError, IndexError) as e:
                                print(f"✗ Error parsing voltage from '{line}': {e}")
                        
                        elif 'Current:' in line:
                            try:
                                current_str = line.split(':')[1].strip().split('A')[0].strip()
                                parsed_data['current'] = float(current_str)
                            except (ValueError, IndexError) as e:
                                print(f"✗ Error parsing current from '{line}': {e}")
                        
                        elif 'Power:' in line:
                            try:
                                power_str = line.split(':')[1].strip().split('W')[0].strip()
                                parsed_data['power'] = float(power_str)
                            except (ValueError, IndexError) as e:
                                print(f"✗ Error parsing power from '{line}': {e}")
                        
                        elif 'Percent:' in line:
                            try:
                                parts = line.split(':')[1].strip().split('%')[0].strip()
                                parsed_data['percent'] = float(parts)
                            except (ValueError, IndexError) as e:
                                print(f"✗ Error parsing percent from '{line}': {e}")
                    
                    # Update all values
                    if 'voltage' in parsed_data:
                        self.robot_status["battery_voltage"] = parsed_data['voltage']
                        dpg.set_value("battery_voltage", f"{parsed_data['voltage']:.2f} V")
                        
                    if 'current' in parsed_data:
                        self.robot_status["battery_current"] = parsed_data['current']
                        dpg.set_value("battery_current", f"{parsed_data['current']:.3f} A")
                        
                    if 'power' in parsed_data:
                        self.robot_status["battery_power"] = parsed_data['power']
                        dpg.set_value("battery_power", f"{parsed_data['power']:.2f} W")
                        
                    if 'percent' in parsed_data:
                        percent = parsed_data['percent']
                        self.robot_status["battery"] = percent
                        
                        dpg.set_value("battery_percent_text", f"{percent:.1f}%")
                        dpg.set_value("battery_gauge", percent / 100.0)
                        
                        # Update color based on battery level
                        if percent > 50:
                            color = (0, 255, 0)  # Green
                        elif percent > 20:
                            color = (255, 165, 0)  # Orange
                        else:
                            color = (255, 0, 0)  # Red
                        
                        dpg.configure_item("battery_percent_text", color=color)
                        dpg.configure_item("battery_gauge", overlay=f"Battery: {percent:.1f}%")
                        
                        # print(f"✓✓✓ Battery fully updated: {percent:.1f}% ({parsed_data.get('voltage', 0):.2f}V)")
                    
                time.sleep(10)
                
            except Exception as e:
                print(f"✗✗✗ Battery monitoring error: {e}")
                time.sleep(10)
    
    def restart_scanner_service(self):
        """Restart scanner service"""
        if not self.connected:
            return
        
        try:
            stdout, stderr = self.execute_command("sudo systemctl restart scanner.service")
            if stderr:
                print(f"Restart service error: {stderr}")
            else:
                print("Scanner service restarted")
        except Exception as e:
            print(f"Error restarting service: {e}")

    def start_scan_motor(self):
        """Start the scan motor"""
        if not self.connected:
            return
        
        try:
            stdout, stderr = self.execute_command("sudo systemctl start run_scan_mtr.service")
            if stderr:
                print(f"Start service error: {stderr}")
            else:
                print("Scan motor started")
        except Exception as e:
            print(f"Error starting service: {e}")

    def stop_scan_motor(self):
        """Stop the scan motor"""
        if not self.connected:
            return
        
        try:
            stdout, stderr = self.execute_command("sudo systemctl stop run_scan_mtr.service")
            if stderr:
                print(f"Stop service error: {stderr}")
            else:
                print("Scan motor stopped")
        except Exception as e:
            print(f"Error stopping service: {e}")

    def start_odometer_service(self):
        """Start odometer service"""
        if not self.connected:
            return
        
        try:
            stdout, stderr = self.execute_command("sudo systemctl start odometer.service")
            if stderr:
                print(f"Start service error: {stderr}")
            else:
                print("Odometer service started")
        except Exception as e:
            print(f"Error starting service: {e}")

    def stop_odometer_service(self):
        """Stop odometer service"""
        if not self.connected:
            return
        
        try:
            stdout, stderr = self.execute_command("sudo systemctl stop odometer.service")
            if stderr:
                print(f"Stop service error: {stderr}")
            else:
                print("Odometer service stopped")
        except Exception as e:
            print(f"Error stopping service: {e}")

    def monitor_status(self):
        """Background thread to monitor robot status"""
        while self.running and self.connected:
            try:
                # Get CPU temperature
                output, _ = self.execute_command("cat /sys/class/thermal/thermal_zone0/temp")
                if output:
                    self.robot_status["cpu_temp"] = float(output) / 1000.0
                    dpg.set_value("cpu_temp", f"CPU Temp: {self.robot_status['cpu_temp']:.1f}°C")
                
                # Get CPU usage
                output, _ = self.execute_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1")
                if output:
                    self.robot_status["cpu_usage"] = float(output)
                    dpg.set_value("cpu_usage", f"CPU Usage: {self.robot_status['cpu_usage']:.1f}%")
                    self.cpu_history.append(self.robot_status["cpu_usage"])
                    if len(self.cpu_history) > 60:
                        self.cpu_history.pop(0)
                
                # Get memory usage
                output, _ = self.execute_command("free | grep Mem | awk '{print ($3/$2) * 100.0}'")
                if output:
                    self.robot_status["memory_usage"] = float(output)
                    dpg.set_value("memory_usage", f"Memory: {self.robot_status['memory_usage']:.1f}%")
                    self.memory_history.append(self.robot_status["memory_usage"])
                    if len(self.memory_history) > 60:
                        self.memory_history.pop(0)
                
                # Get disk usage
                output, _ = self.execute_command("df -h / | tail -1 | awk '{print $5}' | cut -d'%' -f1")
                if output:
                    self.robot_status["disk_usage"] = float(output)
                    dpg.set_value("disk_usage", f"Disk: {self.robot_status['disk_usage']:.1f}%")

                # Update time points
                self.time_points.append(self.time_counter)
                self.time_counter += 1
                if len(self.time_points) > 60:
                        self.time_points.pop(0)

                # keep time points between 0 and 59
                for i in range(len(self.time_points)):
                    self.time_points[i] = i
                
                # Update plots
                self.update_plots()
                
                time.sleep(1)  # Update once per second
                
            except Exception as e:
                print(f"Status monitoring error: {e}")
                time.sleep(2)
    
    def process_commands(self):
        """Background thread to process command queue"""
        while self.running and self.connected:
            try:
                if not self.command_queue.empty():
                    command = self.command_queue.get()
                    output, error = self.execute_command(command)
                    
                    if output:
                        self.response_queue.put(output)
                        self.log_command(f"$ {command}\n{output}")
                    if error:
                        self.log_command(f"Error: {error}")
                
                time.sleep(0.1)
            except Exception as e:
                print(f"Command processing error: {e}")
    
    def send_robot_command(self, command_type, **kwargs):
        """Send command to robot control script"""
        # Construct command based on your robot's control interface
        # This assumes you have a Python script on the Pi that controls the robot
        
        if command_type == "velocity":
            linear = kwargs.get("linear", 0.0)
            angular = kwargs.get("angular", 0.0)
            cmd = f"python3 /home/doug/robot_control.py --velocity {linear} {angular}"
            
        elif command_type == "move":
            direction = kwargs.get("direction", "stop")
            cmd = f"python3 /home/doug/robot_control.py --move {direction}"
            
        elif command_type == "stop":
            cmd = f"python3 /home/doug/robot_control.py --stop"
            
        elif command_type == "emergency_stop":
            cmd = f"python3 /home/doug/robot_control.py --emergency-stop"
            
        elif command_type == "custom":
            cmd = kwargs.get("command", "")
            
        else:
            return
        
        self.command_queue.put(cmd)
        self.log_command(f"Sent: {cmd}")
    
    def log_command(self, message):
        """Add message to command log"""
        timestamp = time.strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.command_log.append(log_entry)
        
        # Update log display
        log_text = "\n".join(self.command_log)
        dpg.set_value("command_log", log_text)
    
    def update_plots(self):
        """Update telemetry plots"""
        if len(self.cpu_history) > 0:
            dpg.set_value(self.cpu_series_tag, [self.time_points, self.cpu_history])
        if len(self.memory_history) > 0:
            dpg.set_value(self.mem_series_tag, [self.time_points, self.memory_history])

        # Force plot refresh
        dpg.fit_axis_data("cpu_x_axis")
        dpg.fit_axis_data("cpu_y_axis")
        dpg.fit_axis_data("mem_x_axis")
        dpg.fit_axis_data("mem_y_axis")
    
    def create_gui(self):
        """Create the GUI layout"""
        dpg.create_context()
        
        # Main window
        with dpg.window(label="Robot SSH Control Center", tag="main_window", width=1400, height=-1):
            
            # Connection section
            with dpg.collapsing_header(label="Connection Settings", default_open=True):
                with dpg.group(horizontal=True):
                    dpg.add_input_text(label="Host", default_value=self.host, 
                                     tag="ssh_host", width=200)
                    dpg.add_input_int(label="Port", default_value=self.port, 
                                    tag="ssh_port", width=100)
                    dpg.add_input_text(label="Username", default_value=self.username, 
                                     tag="ssh_username", width=150)
                    dpg.add_input_text(label="Password", default_value=self.password, 
                                     tag="ssh_password", password=True, width=150)
                
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(label="Use SSH Key", tag="use_key", 
                                   callback=lambda: self.toggle_key_auth())
                    dpg.add_input_text(label="Key Path", tag="key_path", 
                                     default_value="~/.ssh/id_rsa", width=300, enabled=False)
                    dpg.add_button(label="Connect", callback=self.on_connect_button, width=100)
                    dpg.add_button(label="Disconnect", callback=self.on_disconnect_button, width=100)
                    dpg.add_text("Status: Disconnected", tag="connection_status", color=(255, 0, 0))
            
            dpg.add_separator()
            
            # Main content area
            with dpg.group(horizontal=True):

                # Left panel - Control
                with dpg.child_window(width=450, height=-1):  # Use height=-1 on child windows to make them fill available vertical space
                    dpg.add_text("Robot Control", color=(100, 200, 255))
                    dpg.add_separator()
                    
                    # Battery Status with Gauge
                    with dpg.group():
                        dpg.add_text("Battery Status", color=(255, 200, 100))
                        dpg.add_separator()
                        
                        # Battery percentage display (centered) - NO FONT BINDING
                        with dpg.group(horizontal=True):
                            dpg.add_spacer(width=150)
                            dpg.add_text("---%", tag="battery_percent_text", color=(150, 150, 150))
                        
                        # Progress bar as battery gauge
                        dpg.add_progress_bar(tag="battery_gauge", default_value=0.0, width=-1, height=40, 
                                           overlay="Battery Level")
                        
                        dpg.add_spacer(height=5)
                        
                        # Battery details in a table-like layout
                        with dpg.table(header_row=False, borders_innerH=True, borders_outerH=True,
                                      borders_innerV=True, borders_outerV=True):
                            dpg.add_table_column()
                            dpg.add_table_column()
                            
                            with dpg.table_row():
                                dpg.add_text("Voltage:")
                                dpg.add_text("0.00 V", tag="battery_voltage")
                            
                            with dpg.table_row():
                                dpg.add_text("Current:")
                                dpg.add_text("0.000 A", tag="battery_current")
                            
                            with dpg.table_row():
                                dpg.add_text("Power:")
                                dpg.add_text("0.00 W", tag="battery_power")
                        
                    dpg.add_separator()
                    
                    # Emergency stop
                    dpg.add_button(label="EMERGENCY STOP", 
                                 callback=lambda: self.send_robot_command("emergency_stop"),
                                 width=-1, height=50)
                    dpg.add_separator()
                    
                    # Velocity control
                    dpg.add_text("Velocity Control")
                    dpg.add_slider_float(label="Linear Velocity (m/s)", tag="linear_vel_slider",
                                       default_value=0.0, min_value=-1.0, max_value=1.0, 
                                       width=-1)
                    dpg.add_slider_float(label="Angular Velocity (rad/s)", tag="angular_vel_slider",
                                       default_value=0.0, min_value=-2.0, max_value=2.0,
                                       width=-1)
                    
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Send Velocity", callback=self.on_send_velocity,
                                     width=210)
                        dpg.add_button(label="Stop", callback=self.on_stop_robot, width=210)
                    
                    dpg.add_separator()
                    
                    # Preset movements
                    dpg.add_text("Preset Commands")
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Forward", 
                                     callback=lambda: self.send_robot_command("move", direction="forward"),
                                     width=100)
                        dpg.add_button(label="Backward", 
                                     callback=lambda: self.send_robot_command("move", direction="backward"),
                                     width=100)
                        dpg.add_button(label="Left", 
                                     callback=lambda: self.send_robot_command("move", direction="left"),
                                     width=100)
                        dpg.add_button(label="Right", 
                                     callback=lambda: self.send_robot_command("move", direction="right"),
                                     width=100)
                    
                    dpg.add_separator()
                    
                    # Custom command
                    dpg.add_text("Custom SSH Command")
                    dpg.add_input_text(label="", tag="custom_command", 
                                     hint="Enter SSH command", width=-1)
                    dpg.add_button(label="Execute Command", callback=self.on_execute_custom,
                                 width=-1)
                    
                    dpg.add_separator()
                    
                    # System status
                    dpg.add_text("System Status", color=(100, 200, 255))
                    dpg.add_separator()
                    dpg.add_text("CPU Temp: 0.0°C", tag="cpu_temp")
                    dpg.add_text("CPU Usage: 0.0%", tag="cpu_usage")
                    dpg.add_text("Memory: 0.0%", tag="memory_usage")
                    dpg.add_text("Disk: 0.0%", tag="disk_usage")
                    
                    dpg.add_separator()
                    
                    # Quick actions
                    dpg.add_text("Quick Actions", color=(100, 200, 255))
                    dpg.add_separator()

                    dpg.add_button(label="Restart Scanner Service",
                                   callback=lambda: self.restart_scanner_service(),
                                   width=-1)

                    dpg.add_button(label="Start Scan Motor",
                                   callback=lambda: self.start_scan_motor(),
                                   width=-1)

                    dpg.add_button(label="Stop Scan Motor",
                                   callback=lambda: self.stop_scan_motor(),
                                   width=-1)

                    dpg.add_button(label="Start Odometer Service",
                                   callback=lambda: self.start_odometer_service(),
                                   width=-1)

                    dpg.add_button(label="Stop Odometer Service",
                                   callback=lambda: self.stop_odometer_service(),
                                   width=-1)

                    dpg.add_button(label="List Home Directory", 
                                 callback=lambda: self.command_queue.put("ls -la ~/"),
                                 width=-1)

                    dpg.add_button(label="Show Current Directory", 
                                 callback=lambda: self.command_queue.put("pwd"),
                                 width=-1)

                    dpg.add_button(label="Reboot Pi", 
                                 callback=lambda: self.on_reboot_pi(),
                                 width=-1)

                # Middle panel - Telemetry
                with dpg.child_window(width=450, height=-1):
                    dpg.add_text("System Telemetry", color=(100, 200, 255))
                    dpg.add_separator()
                    
                    # CPU plot
                    with dpg.plot(label="CPU Usage", height=250, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag="cpu_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="CPU %", tag="cpu_y_axis")
                        self.cpu_series_tag = dpg.add_line_series(self.time_points, self.cpu_history,
                                                                  label="CPU", parent="cpu_y_axis")
                    
                    # Memory plot
                    with dpg.plot(label="Memory Usage", height=250, width=-1):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag="mem_x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="Memory %", tag="mem_y_axis")
                        self.mem_series_tag = dpg.add_line_series(self.time_points, self.memory_history,
                                                                  label="Memory", parent="mem_y_axis")
                    
                    dpg.add_separator()

                # Right panel - Command log and file browser
                with dpg.child_window(width=-1, height=-1):
                    dpg.add_text("Command Log & Output", color=(100, 200, 255))
                    dpg.add_separator()
                    
                    # Command log
                    dpg.add_input_text(tag="command_log", multiline=True, 
                                     readonly=True, height=300, width=-1,
                                     default_value="Waiting for connection...")
                    
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Clear Log", callback=self.on_clear_log, width=150)
                        dpg.add_button(label="Save Log", callback=self.on_save_log, width=150)
                    
                    dpg.add_separator()
                    
                    # File operations
                    dpg.add_text("File Operations", color=(100, 200, 255))
                    dpg.add_separator()
                    
                    dpg.add_input_text(label="Remote Path", tag="remote_path",
                                     default_value="/home/doug/", width=-1)
                    
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="List Files", callback=self.on_list_files, width=150)
                        dpg.add_button(label="Upload File", callback=self.on_upload_file, width=150)
                        dpg.add_button(label="Download File", callback=self.on_download_file, width=150)
                    
                    dpg.add_separator()
                    
                    # File list
                    dpg.add_text("File Browser")
                    dpg.add_listbox([], tag="file_list", width=-1, num_items=10)
                    
                    dpg.add_separator()
                    
                    # Process management
                    dpg.add_text("Process Management", color=(100, 200, 255))
                    dpg.add_separator()
                    
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="List Processes", 
                                     callback=lambda: self.command_queue.put("ps aux | grep python"),
                                     width=150)
                        dpg.add_button(label="Kill Process", callback=self.on_kill_process, width=150)
                    
                    dpg.add_input_text(label="PID", tag="process_pid", width=150)

                    # Camera Feed
                    with dpg.group():
                        dpg.add_text("Camera Feed", color=(100, 200, 255))
                        
                        # Camera controls
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label="Start Camera",
                                tag="camera_toggle_btn",
                                callback=lambda: self.toggle_camera()
                            )
                        
                        # Camera image
                        dpg.add_image("camera_texture", width=640, height=480)

        # File dialogs
        with dpg.file_dialog(directory_selector=False, show=False, 
                           callback=self.upload_file_callback, tag="upload_dialog",
                           width=700, height=400):
            dpg.add_file_extension(".*")
            dpg.add_file_extension(".py", color=(0, 255, 0, 255))
            dpg.add_file_extension(".txt", color=(255, 255, 0, 255))
            dpg.add_file_extension(".json", color=(255, 0, 255, 255))
        
        with dpg.file_dialog(directory_selector=False, show=False,
                           callback=self.download_file_callback, tag="download_dialog",
                           width=700, height=400):
            dpg.add_file_extension(".*")
        
        # Setup and show
        dpg.create_viewport(title="Robot SSH Control GUI", width=1420, height=900)
        dpg.setup_dearpygui()
        # Force the viewport to the desired size
        dpg.set_viewport_width(1400)
        dpg.set_viewport_height(1200)
        dpg.show_viewport()
        dpg.set_primary_window("main_window", True)
        dpg.start_dearpygui()
        dpg.destroy_context()

    def toggle_key_auth(self):
        """Toggle SSH key authentication"""
        use_key = dpg.get_value("use_key")
        dpg.configure_item("key_path", enabled=use_key)
        dpg.configure_item("ssh_password", enabled=not use_key)
    
    def on_connect_button(self):
        """Handle connect button click"""
        import os
        
        self.host = dpg.get_value("ssh_host")
        self.port = dpg.get_value("ssh_port")
        self.username = dpg.get_value("ssh_username")
        self.password = dpg.get_value("ssh_password")
        self.use_key = dpg.get_value("use_key")
        self.key_path = os.path.expanduser(dpg.get_value("key_path"))  # Expand ~ to home directory
        
        # Validate inputs
        if not self.host:
            self.log_command("Error: Host cannot be empty")
            return
        
        if not self.username:
            self.log_command("Error: Username cannot be empty")
            return
        
        if not self.use_key and not self.password:
            self.log_command("Error: Password required (or enable SSH Key)")
            return
    
        # Connect in separate thread to avoid blocking GUI
        self.log_command(f"Connecting to {self.username}@{self.host}:{self.port}...")
        threading.Thread(target=self.setup_ssh_connection, daemon=True).start()

    def on_disconnect_button(self):
            """Handle disconnect button click"""
            self.disconnect_ssh()
    
    def on_send_velocity(self):
        """Handle send velocity button click"""
        linear = dpg.get_value("linear_vel_slider")
        angular = dpg.get_value("angular_vel_slider")
        self.send_robot_command("velocity", linear=linear, angular=angular)
    
    def on_stop_robot(self):
        """Handle stop button click"""
        self.send_robot_command("stop")
        dpg.set_value("linear_vel_slider", 0.0)
        dpg.set_value("angular_vel_slider", 0.0)
    
    def on_execute_custom(self):
        """Execute custom SSH command"""
        command = dpg.get_value("custom_command")
        if command:
            self.command_queue.put(command)
            dpg.set_value("custom_command", "")
    
    def on_clear_log(self):
        """Clear command log"""
        self.command_log.clear()
        dpg.set_value("command_log", "")
    
    def on_save_log(self):
        """Save command log to file"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"robot_log_{timestamp}.txt"
        try:
            with open(filename, 'w') as f:
                f.write("\n".join(self.command_log))
            self.log_command(f"Log saved to {filename}")
        except Exception as e:
            self.log_command(f"Failed to save log: {e}")
    
    def on_list_files(self):
        """List files in remote directory"""
        remote_path = dpg.get_value("remote_path")
        command = f"ls -lah {remote_path}"
        self.command_queue.put(command)
        
    # Also get file list for listbox
    def get_files():
        output, _ = self.execute_command(f"ls {remote_path}")
        if output:
            files = output.split('\n')
            dpg.configure_item("file_list", items=files)
        
        threading.Thread(target=get_files, daemon=True).start()
    
    def on_upload_file(self):
        """Show upload file dialog"""
        dpg.show_item("upload_dialog")
    
    def on_download_file(self):
        """Show download file dialog"""
        dpg.show_item("download_dialog")
    
    def upload_file_callback(self, sender, app_data):
        """Handle file upload"""
        local_path = app_data['file_path_name']
        remote_path = dpg.get_value("remote_path")
        
    def upload():
        try:
            sftp = self.ssh_client.open_sftp()
            remote_file = remote_path + "/" + local_path.split('/')[-1]
            sftp.put(local_path, remote_file)
            sftp.close()
            self.log_command(f"Uploaded {local_path} to {remote_file}")
        except Exception as e:
            self.log_command(f"Upload failed: {e}")
    
        threading.Thread(target=upload, daemon=True).start()
    
    def download_file_callback(self, sender, app_data):
        """Handle file download"""
        local_path = app_data['file_path_name']
        
        # Get selected file from listbox
        selected_files = dpg.get_value("file_list")
        if not selected_files:
            self.log_command("No file selected for download")
            return
        
        remote_path = dpg.get_value("remote_path")
        remote_file = remote_path + "/" + selected_files
        
    def download():
        try:
            sftp = self.ssh_client.open_sftp()
            sftp.get(remote_file, local_path)
            sftp.close()
            self.log_command(f"Downloaded {remote_file} to {local_path}")
        except Exception as e:
            self.log_command(f"Download failed: {e}")
        
        threading.Thread(target=download, daemon=True).start()
    
    def on_kill_process(self):
        """Kill process by PID"""
        pid = dpg.get_value("process_pid")
        if pid:
            command = f"kill -9 {pid}"
            self.command_queue.put(command)
    
    def on_reboot_pi(self):
        """Reboot Raspberry Pi with confirmation"""
        self.log_command("Rebooting Raspberry Pi...")
        self.command_queue.put("sudo reboot")
        # Disconnect after a delay
        threading.Timer(2.0, self.on_disconnect_button).start()
    
    def run(self):
        """Run the GUI application"""
        dpg.create_context()
        self.setup_camera_texture()  # Create texture before GUI
        self.create_gui()
        
        # Main loop
        while dpg.is_dearpygui_running():
            dpg.render_dearpygui_frame()
            time.sleep(0.016)  # ~60 FPS
        
        # Cleanup
        self.disconnect_ssh()
        dpg.destroy_context()


if __name__ == "__main__":
    # Create and run the GUI
    # Update these with your Raspberry Pi details
    gui = RobotSSHGUI(
        host="raspibot.local",  # Your Pi's IP address
        port=22,
        username="doug",
        password="robot"  # Or use SSH key
    )
    gui.run()
