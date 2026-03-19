#!/usr/bin/env python3
# robot_control.py - Place this on your Raspberry Pi

import argparse
import sys
import time

class RobotController:
    def __init__(self):
        # Initialize your robot hardware here
        # This is a placeholder - adapt to your actual hardware
        self.current_velocity = {"linear": 0.0, "angular": 0.0}
        
    def set_velocity(self, linear, angular):
        """Set robot velocity"""
        self.current_velocity = {"linear": linear, "angular": angular}
        print(f"Setting velocity: linear={linear}, angular={angular}")
        
        # Add your actual motor control code here
        # For example, if using GPIO or motor controller library:
        # self.left_motor.set_speed(linear - angular)
        # self.right_motor.set_speed(linear + angular)
        
    def move(self, direction):
        """Move robot in specified direction"""
        movements = {
            "forward": (0.5, 0.0),
            "backward": (-0.5, 0.0),
            "left": (0.0, 0.5),
            "right": (0.0, -0.5),
            "stop": (0.0, 0.0)
        }
        
        if direction in movements:
            linear, angular = movements[direction]
            self.set_velocity(linear, angular)
            print(f"Moving {direction}")
        else:
            print(f"Unknown direction: {direction}")
    
    def stop(self):
        """Stop robot"""
        self.set_velocity(0.0, 0.0)
        print("Robot stopped")
    
    def emergency_stop(self):
        """Emergency stop - immediately halt all movement"""
        self.stop()
        print("EMERGENCY STOP ACTIVATED")
        # Add any additional safety measures here


def main():
    parser = argparse.ArgumentParser(description='Robot Control Script')
    parser.add_argument('--velocity', nargs=2, type=float, metavar=('LINEAR', 'ANGULAR'),
                       help='Set velocity (linear angular)')
    parser.add_argument('--move', choices=['forward', 'backward', 'left', 'right', 'stop'],
                       help='Move in direction')
    parser.add_argument('--stop', action='store_true', help='Stop robot')
    parser.add_argument('--emergency-stop', action='store_true', help='Emergency stop')
    
    args = parser.parse_args()
    
    robot = RobotController()
    
    if args.velocity:
        linear, angular = args.velocity
        robot.set_velocity(linear, angular)
    elif args.move:
        robot.move(args.move)
    elif args.stop:
        robot.stop()
    elif args.emergency_stop:
        robot.emergency_stop()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
