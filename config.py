import keyboard
import customtkinter
import numpy
import tkinter as tk
import sounddevice as sd
import ffmpeg
import csv
from PIL import Image, ImageDraw
import pystray
import threading
import mss
import time
import subprocess

root = tk.Tk()
SCREEN_WIDTH = root.winfo_screenwidth()
SCREEN_HEIGHT = root.winfo_screenheight()
root.destroy()

def create_icon_image():
    """Generates a simple 64x64 blue square icon image for the tray."""
    img = Image.new('RGB', (64, 64), color='#1f538d')
    # Optional: Draw a tiny white dot or design inside it
    d = ImageDraw.Draw(img)
    d.rectangle([(16, 16), (48, 48)], fill='white')
    return img

TRAY_ICON = create_icon_image() # Stores the icon image for if it is minimized to tray

with open('defaults.csv','r') as csvfile:
    reader = csv.reader(csvfile)

    for line in reader:
        if line[0] == 'clip_key':
            CLIP_KEY = line[1]
