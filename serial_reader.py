import serial
import os
from typing import Final


class SerialReader:
    """Class to read from a serial device"""
    LOG_DEBUG: Final = 7
    LOG_ERR: Final = 3
    LOG_INFO: Final = 6
    LOG_NOTICE: Final = 5
    LOG_WARNING: Final = 4

    def __init__(self, device):
        """Initialize the serial reader"""
        # Connect to serial device that provides the Meshtastic debug log
        try:
            self.interface = serial.Serial(
                device, baudrate=115200, bytesize=8, parity='N', stopbits=1, timeout=5)

        except PermissionError as ex:
            username = os.getlogin()
            message = "Permission Error:\n"
            message += "  Need to add yourself to the 'dialout' group by running:\n"
            message += f"     sudo usermod -a -G dialout {username}\n"
            message += "  After running that command, log out and re-login for it to take effect.\n"
            message += f"Error was:{ex}"
            self.log(message, level=self.LOG_ERR)

        except serial.SerialException as e:
            self.log(
                f"Serial interface not found. Error was: {e}", level=self.LOG_ERR)

    def poll_read(self):
        """Read a line from the serial port"""
        line = []
        try:
            if not self.interface.is_open:
                self.interface.open()
            self.interface.timeout = 5
            line.append(self.interface.readline().decode(
                'utf-8', errors='ignore').strip())
        except serial.SerialException as e:
            self.log(f"Error reading serial device: {e}", level=self.LOG_ERR)
            self.interface.close()
            return [None]
        
        return line

    def is_open(self):
        """Check if the serial port is open"""
        try:
            return self.interface.is_open
        except AttributeError:
            return False

    def close(self):
        """Close the serial port"""
        self.interface.close()

    def log(self, message, level=LOG_INFO):
        """Log a message to the serial port."""
        match level:
            case self.LOG_DEBUG:
                print(f"\x1b[2;37;49m{message}\x1b[0m")
            case self.LOG_ERR:
                print(f"\x1b[0;31;49m{message}\x1b[0m")
            case self.LOG_INFO:
                print(message)
            case self.LOG_NOTICE:
                print(f"\x1b[0;36;49m{message}\x1b[0m")
            case self.LOG_WARNING:
                print(f"\x1b[0;33;49m{message}\x1b[0m")

    def __enter__(self):
        """Enter the runtime context related to this object"""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context and close the serial port"""
        self.close()
