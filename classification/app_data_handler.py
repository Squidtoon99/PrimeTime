import os
import traceback
import win32gui
import win32con
import win32ui
import win32api
import win32process
import json
from datetime import datetime
from PIL import ImageGrab, Image
import psutil
import ctypes
import ctypes.wintypes
from redis import Redis

# Define constants and configurations
POLLING_INT = 0.5
CURRENT_PATH = os.getcwd()
SCREENSHOT_PATH = os.path.join(CURRENT_PATH, "screenshots")
ICON_PATH = os.path.join(CURRENT_PATH, "icons")
SS_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Ensure the directories for screenshots and icons exist.
os.makedirs(SCREENSHOT_PATH, exist_ok=True)
os.makedirs(ICON_PATH, exist_ok=True)

# Make the process DPI-aware
ctypes.windll.user32.SetProcessDPIAware()


# Initialize Redis client
def init_redis_client():
    try:
        client = Redis(decode_responses=True)
        return client
    except Exception as e:
        print(f"Error connecting to Redis: {e}")
        return None


# Gets the handle (identifier) and title of the currently active (foreground) window.
def get_win():
    hwnd = win32gui.GetForegroundWindow()  # Get handle of the active window.
    win_title = win32gui.GetWindowText(hwnd)  # Get the title of the active window.
    return hwnd, win_title


# Captures a screenshot of the window specified by hwnd, saves it with a timestamp, and returns the file path.
def screenshot(hwnd):
    try:
        # Get the window coordinates for cropping the screenshot.
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        # Capture the screenshot within the window's bounding box.
        ss = ImageGrab.grab(bbox=(left, top, right, bottom))

        # Generate a timestamped filename and save the screenshot.
        timestamp = datetime.now().strftime(SS_TIMESTAMP_FORMAT)
        ss_path = os.path.join(SCREENSHOT_PATH, f"screenshot_{timestamp}.png")
        ss.save(ss_path)

        return ss_path  # Return the path where the screenshot was saved.
    except Exception as e:
        print(f"Error taking screenshot: {e}")
        return None


# Extracts the application icon of the process with pid and saves it as a PNG image.
def save_icon(hwnd, pid):
    try:
        path = psutil.Process(pid).exe()  # Get the executable path of the process.
        ico_x = win32api.GetSystemMetrics(win32con.SM_CXICON)  # Standard icon width
        ico_y = win32api.GetSystemMetrics(win32con.SM_CYICON)  # Standard icon height

        # Extract large and small icons from the executable.
        large, small = win32gui.ExtractIconEx(path, 0)
        if small:
            win32gui.DestroyIcon(small[0])  # Clean up the small icon if it exists.

        # Set up a bitmap and device context for the icon.
        hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        hbmp = win32ui.CreateBitmap()
        hbmp.CreateCompatibleBitmap(hdc, ico_x, ico_y)
        hdc = hdc.CreateCompatibleDC()

        # Draw the icon on the bitmap.
        hdc.SelectObject(hbmp)
        if large:
            hdc.DrawIcon((0, 0), large[0])

        # Save the icon as a temporary bitmap file.
        temp_path = os.path.join(ICON_PATH, "save.bmp")
        hbmp.SaveBitmapFile(hdc, temp_path)
        hdc.DeleteDC()

        # Convert the bitmap file to PNG format.
        bmp_image = Image.open(temp_path)
        icon_path = os.path.join(ICON_PATH, f"{psutil.Process(pid).name()}.png")
        bmp_image.save(icon_path, "PNG")

        os.remove(temp_path)  # Delete the temporary bitmap file.

        return icon_path  # Return the path to the saved icon.
    except Exception as e:
        print(f"Error saving icon: {e}")
        return None


# Retrieves a readable name (e.g., application name) from the executable file path.
def get_readable_exe_name(exe_path):
    try:
        version = (
            ctypes.windll.version
        )  # Load the Windows version API for metadata extraction.

        # Get the version info size and allocate a buffer to store this info.
        size = version.GetFileVersionInfoSizeW(exe_path, None)
        if size == 0:
            return os.path.splitext(os.path.basename(exe_path))[0]

        # Load version information into a buffer.
        res = ctypes.create_string_buffer(size)
        success = version.GetFileVersionInfoW(exe_path, 0, size, res)
        if not success:
            return os.path.splitext(os.path.basename(exe_path))[0]

        # Retrieve the translation info (language and codepage) for the executable.
        rctypes = ctypes.c_uint
        lplpBuffer = ctypes.c_void_p()
        puLen = rctypes(0)
        success = version.VerQueryValueW(
            res,
            "\\VarFileInfo\\Translation",
            ctypes.byref(lplpBuffer),
            ctypes.byref(puLen),
        )

        # Define default language and codepage if not found.
        languages = [(1033, 1200)] if not success else [(lang, codepage)]

        for lang, codepage in languages:
            for key in ("FileDescription", "ProductName"):
                str_info_path = "\\StringFileInfo\\%04x%04x\\%s" % (lang, codepage, key)
                lplpBuffer = ctypes.c_void_p()
                puLen = rctypes(0)
                success = version.VerQueryValueW(
                    res, str_info_path, ctypes.byref(lplpBuffer), ctypes.byref(puLen)
                )
                if success and puLen.value > 0:
                    value = ctypes.wstring_at(lplpBuffer, puLen.value)
                    clean_value = value.replace("\x00", "").strip()
                    if clean_value:
                        return clean_value  # Return the clean readable name if found.

        # Fallback to the file name if description not found.
        return os.path.splitext(os.path.basename(exe_path))[0]
    except Exception as e:
        print(f"Error retrieving name: {e}")
        return os.path.splitext(os.path.basename(exe_path))[0]


# Retrieves the name and PID (Process ID) of the application associated with a window handle.
def process_app(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(
            hwnd
        )  # Get the PID of the window.
        process = psutil.Process(pid)  # Use psutil to get process info.
        return process.name(), pid  # Return the process name and PID.
    except Exception as e:
        print(f"Error retrieving process information: {e}")
        return None, None


# Handles console events such as close, logoff, or shutdown, sending a 'system closing' event to Redis.
def console_event_handler(event, prev_uuid, client):
    if event in [
        win32con.CTRL_CLOSE_EVENT,
        win32con.CTRL_LOGOFF_EVENT,
        win32con.CTRL_SHUTDOWN_EVENT,
    ]:
        # Log the closing event with a timestamp and minimal details.
        timestamp = datetime.now().timestamp()
        closing_event = {
            "id": prev_uuid,
            "timestamp": timestamp,
            "classification": "system",
            "app_name": "ActivityMonitor",
            "win_title": "",
            "state": False,
            "screenshot": None,
            "icon": None,
        }
        try:
            if client and prev_uuid != None:
                client.xadd("activity", {"data": json.dumps(closing_event)})
                print(json.dumps(closing_event, indent=4))
        except Exception as e:
            print(f"Error sending closing event to Redis: {e}")
            traceback.print_exc()
        return True  # Signal that event has been handled.
    return False


# Sends a 'system closing' event to Redis on program exit, ensuring a clean shutdown.
def cleanup(prev_uuid, client):
    timestamp = datetime.now().timestamp()
    closing_event = {
        "id": prev_uuid,
        "timestamp": timestamp,
        "classification": "system",
        "app_name": "ActivityMonitor",
        "win_title": "",
        "state": False,
        "screenshot": None,
        "icon": None,
    }
    try:
        if client and prev_uuid != None:
            client.xadd("activity", {"data": json.dumps(closing_event)})
            print(json.dumps(closing_event, indent=4))
    except Exception as e:
        print(f"Error sending closing event to Redis during cleanup: {e}")


# Clean up files if 'delete' is set to True.
def clean_up_files(icon_path, screenshot_path, delete):
    if delete:
        if icon_path and os.path.exists(icon_path):
            os.remove(icon_path)
        if screenshot_path and os.path.exists(screenshot_path):
            os.remove(screenshot_path)
