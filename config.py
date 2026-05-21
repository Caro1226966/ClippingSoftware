import keyboard
import customtkinter
import sounddevice as sd
import ffmpeg
import csv

app = customtkinter.CTk()
app.geometry('800X500')

with open('defaults.csv','r') as csvfile:
    reader = csv.reader(csvfile)

    for line in reader:
        if line[0] == 'clip_key':
            CLIP_KEY = line[1]
