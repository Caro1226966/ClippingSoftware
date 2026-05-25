import threading

from config import *
from button_callbacks import *


class MainProgram(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.geometry(str(int(SCREEN_WIDTH/1.5))+'x'+ str(int(SCREEN_HEIGHT/1.5)))

        # App logic flags
        self.clip_key_pressed = False
        self.popup_active = False

        # Monitor settings
        self.monitor_list = []

        # SCT
        self.sct = mss.MSS()
        self.video_list = []
        self.monitor = None
        self.last_frame_time = time.time()

        # Button Callbacks
        self.button_callback = Callbacks(self)

        # The clipping key to choose an active button
        self.choose_clipping_key = customtkinter.CTkButton(self, text='Select Clip Button',command = self.button_callback.setbutton)
        self.choose_clipping_key.pack(side="right", anchor="ne", padx=10, pady=30)

        # Text to show what button is the active clipping button
        self.current_button_text = customtkinter.CTkTextbox(self,width=200, height=30)
        self.current_button_text.pack(side="right", anchor="ne", padx=20, pady=30) # Padding
        self.current_button_text.insert(index=0.0,text=('Current Key: '+str(self.button_callback.clipping_key))) # Put in the text
        self.current_button_text.configure(state="disabled")

        # Monitor selector
        for monitor in range(len(self.sct.monitors)-1):
            monitor_num = str(monitor)
            (self.monitor_list.append
             (monitor_num))
        print(self.monitor_list)

        # Text to show user that dropdown selects the monitor
        self.current_monitor_text = customtkinter.CTkTextbox(self,width=200, height=30)
        self.current_monitor_text.pack(side="left", anchor="ne", padx=20, pady=30) # Padding
        self.current_monitor_text.insert(index=0.0,text='Monitor: ') # Put in the text
        self.current_monitor_text.configure(state="disabled")

        self.monitor_selector = customtkinter.CTkComboBox(self,values=self.monitor_list,
                                                          command=self.button_callback.selectmonitor,
                                                          state ='readonly')
        self.monitor_selector.set(self.read_from_file('monitor'))
        self.monitor_selector.pack(side='left', anchor='nw',pady = 30)

        # Dropdown to select the clip length
        self.monitor_selector = customtkinter.CTkComboBox(self,values=self.monitor_list,
                                                          command=self.button_callback.selectmonitor,
                                                          state ='readonly')
        self.monitor_selector.set(self.read_from_file('monitor'))
        self.monitor_selector.pack(side='left', anchor='nw',pady = 30)

        # Close to system tray
        self.system_tray_setup()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.loop() # The program logic

        # The mainloop that repeats the code above and keeps the window alive
        self.mainloop()

    # This is the program's main logic loop
    def loop(self):
        # print('Called mainloop')
        if keyboard.is_pressed(self.button_callback.clipping_key) and self.clip_key_pressed == False:
            print('Clipping...')
            popup = Popup()

            # Compiling the list into a video clip
            frames_to_compile = list(self.video_list)
            self.last_frame_time = time.time()
            compilation_thread = threading.Thread(target=self.compile_clip,
                                                  args=(frames_to_compile,),
                                                  daemon = True)
            compilation_thread.start()

            self.clip_key_pressed = True

        elif not keyboard.is_pressed(self.button_callback.clipping_key):
            self.clip_key_pressed = False

        self.capture_screen()
        self.after(1,self.loop) # Lets the program logic mainloop keep running alongside GUI

    # Writes whatever the input is to wherever it needs to go
    def capture_screen(self):

        current_time = time.time()

        # Defines the monitor
        self.monitor = self.sct.monitors[self.button_callback.monitor + 1]

        # Calculates frame delay to hit target fps
        elapsed_time = current_time - self.last_frame_time
        frame_delay = 1.0 / self.button_callback.fps

        if elapsed_time >= frame_delay:
            # Gets a screenshot and adds it to the list
            try:
                sct_image= self.sct.grab(self.monitor)
                self.video_list.append(sct_image.bgra)
            except:
                pass

            # Culls unneeded screenshots
            if len(self.video_list) > self.button_callback.fps * self.button_callback.clip_length:
                self.video_list.pop(0)

            self.last_frame_time += frame_delay

# Puts all the screenshots together into a video
    def compile_clip(self,video_list):
        width = self.monitor['width']
        height = self.monitor['height']
        ffmpeg_cmd = [
            'bin/ffmpeg.exe',
            '-y',  # Overwrite output file if it already exists
            '-f', 'rawvideo',  # Input format is raw pixels
            '-vcodec', 'rawvideo',
            '-pix_fmt', 'bgr0',  # MSS bgra data maps perfectly to FFmpeg's bgr0
            '-s', f"{width}x{height}",  # Tell FFmpeg the dimensions of the incoming frames
            '-r', str(self.button_callback.fps),  # Input frame rate
            '-i', '-',  # '-' tells FFmpeg to listen to the incoming RAM pipe
            '-c:v', 'libx264',  # Encode using the standard H.264 video codec
            '-pix_fmt', 'yuv420p',  # Ensure maximum compatibility for media players
            self.button_callback.create_file_name()
        ]

        process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)

        for image in video_list:
            process.stdin.write(image)

        process.stdin.close()
        process.wait()
        print('Clipped!')


    def write_to_file(self,pointer, value):
        # print('Called Write to file')
        updated_file = []
        with open('defaults.csv', 'r') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    print('Written', value, 'to', pointer)
                    line[1] = value

                updated_file.append(line) # adds the line as a new component in the list

        # re-writes the file to put in the new values accurately
        with open('defaults.csv','w',newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerows(updated_file)

    def read_from_file(self, pointer):
        with open('defaults.csv','r') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    return line[1]
            return None

    # Tray Logic ------------------------------------------------------------------------

    # Brings the window back up
    def show_window(self):
        self.deiconify()
        self.lift()

    # Hides the window
    def hide_window(self):
        self.withdraw()

    # Shuts down the app
    def quit_app(self,icon):
        icon.stop()
        self.quit()

    # Initialises the system tray logic
    def system_tray_setup(self):
        # Creates the menu and icon
        menu = pystray.Menu(pystray.MenuItem('Open',self.show_window),
                            pystray.MenuItem('Quit', self.quit_app))
        tray_icon = pystray.Icon('Clipping Software',TRAY_ICON,
                                 'Clipping Software', menu)

        # Runs it in a thread so it doesn't stop the rest of the program
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()


# This is the popup that tells you your clipping
class Popup(customtkinter.CTkToplevel):
    def __init__(self):
        super().__init__()
        self.width = str(int(SCREEN_WIDTH / 7))
        self.height = str(int(SCREEN_HEIGHT / 11))


        # Helps make it rounder
        self.config(background="pink")
        self.attributes('-transparentcolor','pink')
        frame = customtkinter.CTkFrame(self, width = int(self.width), height = int(self.height), fg_color='#545252', corner_radius=20)
        frame.pack(fill="both", expand=True)

        # Removes the title bar
        self.overrideredirect(True)
        self.attributes("-topmost", True)  # Keep on top

        text = customtkinter.CTkLabel(frame,text='Clipping...', font=("Arial", 16))
        text.pack(expand=True)

        self.geometry(self.width + 'x' + self.height)

        # Makes the popup spawn in the right place
        # Format: WidthxHeight+X_Offset+Y_Offset
        # 50 pixels from the left of the screen, 50 pixels down from the top
        self.geometry(self.width + "x" + self.height + "+" + str(SCREEN_WIDTH - int(self.width)) + "+" + str(
            SCREEN_HEIGHT - int(self.height) - int(SCREEN_HEIGHT / 1.2)))

        self.after(1500, self.destroy)


if __name__ == "__main__":
    program = MainProgram()
