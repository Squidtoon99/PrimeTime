import time
import json
import traceback
import ctypes
import win32api
import psutil
import uuid
import atexit
from datetime import datetime
from activity_classifier import ActivityClassifier  # Import the classifier
from app_data_handler import (
    init_redis_client,
    get_win,
    screenshot,
    save_icon,
    get_readable_exe_name,
    process_app,
    console_event_handler,
    cleanup,
    clean_up_files,
    TIMESTAMP_FORMAT,
)

import ctypes.wintypes

# Initialize Redis client
client = init_redis_client()

# Initialize the ActivityClassifier
classifier = ActivityClassifier()

# Initialize global variables
prev_uuid = None

# Make the process DPI-aware
ctypes.windll.user32.SetProcessDPIAware()


# Define LASTINPUTINFO structure for tracking idle time
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("dwTime", ctypes.wintypes.DWORD),
    ]


# Function to get the idle duration in seconds
def get_idle_duration():
    lastInputInfo = LASTINPUTINFO()
    lastInputInfo.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastInputInfo)):
        millis = ctypes.windll.kernel32.GetTickCount() - lastInputInfo.dwTime
        return millis / 1000.0  # Convert milliseconds to seconds
    else:
        return 0


# Handles console events such as close, logoff, or shutdown.
def handle_console_event(event):
    global prev_uuid
    return console_event_handler(event, prev_uuid, client)


# Register the console event handler so it can respond to system events.
win32api.SetConsoleCtrlHandler(handle_console_event, True)


# Registers the cleanup function to ensure it is executed on exit.
def on_exit():
    global prev_uuid
    cleanup(prev_uuid, client)


atexit.register(on_exit)


# Continuously monitors the active window, logging any changes in foreground application.
def check_foreground_win(delete=True):
    prev_win_title = None  # Track the title of the previous active window.
    prev_hwnd = None  # Track the handle of the previous active window.
    prev_app_name = None  # Track the previous application's name.
    prev_icon_path = None  # Track the path to the previous icon.
    global prev_uuid
    global last_state
    prev_uuid = None
    prev_classification = None

    # Initialize timing variables
    last_screenshot_time = time.time()
    screenshot_interval = 180  # 3 minutes in seconds
    idle_threshold = 300  # 5 minutes in seconds

    try:
        while True:
            try:
                current_time = time.time()
                idle_duration = get_idle_duration()

                hwnd, win_title = get_win()  # Get the current active window and title.
                last_state = False

                if (
                    hwnd != prev_hwnd and hwnd != 0
                ):  # Detect a change in the active window.
                    timestamp = current_time
                    app_name, pid = process_app(
                        hwnd
                    )  # Get the app name and process ID of the current window.
                    if pid is None:
                        continue
                    icon_path = save_icon(
                        hwnd, pid
                    )  # Save the application icon and get its path.
                    app_name = (
                        get_readable_exe_name(psutil.Process(pid).exe()) or app_name
                    )  # Get the readable app name.

                    # Log the "closed" event for the previous window if it exists.
                    if prev_win_title:
                        closed_event = {
                            "id": prev_uuid,
                            "timestamp": timestamp,
                            "classification": prev_classification,
                            "app_name": prev_app_name,
                            "win_title": prev_win_title,
                            "state": False,
                            "screenshot": None,
                            "icon": prev_icon_path,
                        }
                        if client:
                            client.xadd("activity", {"data": json.dumps(closed_event)})
                        print(json.dumps(closed_event, indent=4))

                    # Capture a screenshot of the new active window
                    screenshot_path = screenshot(hwnd)
                    u = str(uuid.uuid4())

                    # Determine classification based on idle status
                    if idle_duration >= idle_threshold:
                        classification = "Idle"
                    else:
                        classification = "Unclear"
                        if screenshot_path:
                            try:
                                classification = classifier.classify_activity(
                                    screenshot_path, app_name, win_title
                                )
                            except Exception as e:
                                print(f"Error classifying activity: {e}")
                                traceback.print_exc()
                        else:
                            print("No screenshot available for classification.")

                    opened_event = {
                        "id": u,
                        "timestamp": timestamp,
                        "classification": classification,
                        "app_name": app_name,
                        "win_title": win_title,
                        "state": True,
                        "screenshot": screenshot_path,
                        "icon": icon_path,
                    }
                    print(json.dumps(opened_event, indent=4))

                    if client:
                        client.xadd("activity", {"data": json.dumps(opened_event)})

                    # Clean up files
                    clean_up_files(icon_path, screenshot_path, delete)

                    # Update previous variables
                    prev_win_title = win_title
                    prev_hwnd = hwnd
                    prev_app_name = app_name
                    prev_icon_path = icon_path
                    prev_uuid = u
                    prev_classification = classification

                    # Reset last_screenshot_time
                    last_screenshot_time = current_time

                    if win_title:
                        last_state = True

                else:
                    # Same window as before
                    if current_time - last_screenshot_time >= screenshot_interval:
                        # Time to take a new screenshot
                        timestamp = current_time
                        screenshot_path = screenshot(hwnd)

                        # Determine classification based on idle status
                        if idle_duration >= idle_threshold:
                            classification = "Idle"
                        else:
                            classification = "Unclear"
                            if screenshot_path:
                                try:
                                    classification = classifier.classify_activity(
                                        screenshot_path, prev_app_name, prev_win_title
                                    )
                                except Exception as e:
                                    print(f"Error classifying activity: {e}")
                                    traceback.print_exc()
                            else:
                                print("No screenshot available for classification.")

                        # Create update event with the same UUID
                        update_event = {
                            "id": prev_uuid,
                            "timestamp": timestamp,
                            "classification": classification,
                            "app_name": prev_app_name,
                            "win_title": prev_win_title,
                            "state": True,
                            "screenshot": screenshot_path,
                            "icon": prev_icon_path,
                        }
                        print(json.dumps(update_event, indent=4))

                        if client:
                            client.xadd("activity", {"data": json.dumps(update_event)})

                        # Clean up files
                        clean_up_files(None, screenshot_path, delete)

                        # Update last_screenshot_time and classification
                        last_screenshot_time = current_time
                        prev_classification = classification

                time.sleep(0.5)

            except Exception as e:
                print(f"Error in main loop: {e}")
                traceback.print_exc()
    except KeyboardInterrupt:
        print("Application interrupted by user.")
    except Exception as e:
        print(f"Unhandled exception: {e}")


# Main entry point of the script, starts the monitoring function.
if __name__ == "__main__":
    try:
        check_foreground_win(delete=False)
    except Exception as e:
        print(f"Unhandled exception in main: {e}")
