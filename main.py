from config import *
from button_callbacks import *
from clip_browser import ClipBrowser
from gpu_recorder import GpuRecorder
import queue
import traceback

# The packaged .exe is windowed, so it has no console to print to. Send output
# to a log file instead: printing to a missing stdout would otherwise crash it,
# and this gives users something to look at when something goes wrong.
if IS_FROZEN:
    # Start a fresh log each launch. Right after an update the previous copy may
    # still be shutting down and holding the file; rather than give up (which
    # used to leave the whole session with no log), keep retrying in the
    # background so logging comes alive as soon as the old copy lets go.
    def _open_log():
        import threading
        def _try():
            for _ in range(120):  # up to ~60s of retries, then quietly stop
                try:
                    sys.stdout = sys.stderr = open(LOG_PATH, 'w', buffering=1,
                                                   encoding='utf-8', errors='replace')
                    return
                except Exception:
                    time.sleep(0.5)
        # one quick synchronous attempt so early prints are usually captured
        try:
            sys.stdout = sys.stderr = open(LOG_PATH, 'w', buffering=1,
                                           encoding='utf-8', errors='replace')
        except Exception:
            threading.Thread(target=_try, daemon=True).start()
    _open_log()

_instance_mutex = None
SHOW_EVENT_NAME = 'Caro122ClippingSoftwareShow'


def already_running():
    """True if another copy is already running (it lives in Startup, so a
    double-launch would otherwise fight over the hotkey and audio devices)."""
    global _instance_mutex
    try:
        _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False,
                                                              'Caro122ClippingSoftware')
        return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def create_show_event():
    """Auto-reset event the running copy watches so clicking the desktop /
    Start Menu shortcut opens the window instead of doing nothing."""
    try:
        return ctypes.windll.kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    except Exception:
        return None


def signal_existing_instance():
    """Ask the copy that's already running to bring its window up."""
    try:
        handle = ctypes.windll.kernel32.OpenEventW(0x0002, False, SHOW_EVENT_NAME)
        if handle:
            ctypes.windll.kernel32.SetEvent(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def _make_clips_icon():
    """Draws a little video-camera icon for the clip browser button."""
    img = Image.new('RGBA', (44, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 4, 28, 27], radius=6, fill='#dce4ee')
    d.polygon([(31, 12), (43, 5), (43, 26), (31, 19)], fill='#dce4ee')
    d.polygon([(11, 11), (11, 21), (20, 16)], fill='#1f538d')
    return img


class MainProgram(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.geometry(str(520)+'x'+ str(640))
        self.resizable(False,False)
        self.title("Caro122's Clipping Software")

        # App logic flags
        self.clip_key_pressed = False
        self.popup_active = False
        self._show_event = create_show_event()
        # Work handed over from background threads (tray icon, etc.) to be run on
        # the Tk main thread — touching widgets from another thread crashes Tcl
        self._ui_queue = queue.Queue()
        self._tray_icon = None
        # Log Tk callback errors instead of letting them tear the program down
        self.report_callback_exception = self._log_exception

        # Monitor settings
        self.monitor_list = []

        # FPS
        self.fps_options = ['10','15','24','30','60','120','240']

        # Clip
        self.clip_options = ['10s','30s','1m','2m','5m','10m']

        # SCT (main-thread instance is only used for enumerating monitors in the GUI)
        self.sct = mss.MSS()
        self.monitor = self.sct.monitors[1] if len(self.sct.monitors) > 1 else self.sct.monitors[0]

        # Button Callbacks
        self.button_callback = Callbacks(self)

        # Rolling audio capture (mic + computer audio)
        self.audio = AudioManager(self)

        # Threaded screen capture (keeps the grab/encode off the GUI thread).
        # This is the CPU path, used as a fallback.
        self.screen = ScreenCapture(self)

        # GPU-native replay buffer (ddagrab + hardware NVENC/AMF/QSV). When a
        # hardware encoder is present this is used instead of self.screen: capture
        # and encode both stay on the GPU, so a game maxing the graphics cores
        # can't starve it the way the CPU readback path gets starved. It falls
        # back to self.screen automatically if it can't start.
        self.gpu = GpuRecorder(self)
        self.use_gpu = self.gpu._uses_hw()

        # The clip browser window (opened from the little video icon).
        # The button itself is created last so it stacks above everything else.
        self.clip_browser = None

        # The colloum that all the UI emelents sit in
        self.left_column = customtkinter.CTkFrame(self, fg_color="transparent")
        self.left_column.pack(side="left", fill="y", anchor="nw", padx=30, pady=30)

        # Row for active clipping button UI elements to sit in
        self.keybind_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.keybind_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show what button is the active clipping button
        self.current_button_text = customtkinter.CTkTextbox(self.keybind_row,width=200, height=30)
        self.current_button_text.pack(side = 'left', anchor="w", padx=10, pady=0) # Padding
        self.current_button_text.insert(index=0.0,text=('Current Key: '+str(self.button_callback.clipping_key))) # Put in the text
        self.current_button_text.configure(state="disabled")

        # The clipping key to choose an active button
        self.choose_clipping_key = customtkinter.CTkButton(self.keybind_row, text='Select Clip Button',command = self.button_callback.setbutton)
        self.choose_clipping_key.pack(side = 'left', anchor="w", padx=0, pady=0)

        # Row for monitor selector UI elements to sit in
        self.monitor_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.monitor_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Monitor selector
        for monitor in range(len(self.sct.monitors)-1):
            monitor_num = str(monitor)
            (self.monitor_list.append(monitor_num))

        # Text to show user that dropdown selects the monitor
        self.current_monitor_text = customtkinter.CTkLabel(self.monitor_row,width=200, height=30, text='Monitor: ')
        self.current_monitor_text.pack(side = 'left', anchor="w", padx=0, pady=0) # Padding
        self.current_monitor_text.configure(state="disabled")

        self.monitor_selector = customtkinter.CTkComboBox(self.monitor_row,values=self.monitor_list,
                                                          command=self.button_callback.selectmonitor,
                                                          state ='readonly')
        self.monitor_selector.set(self.read_from_file('monitor'))
        self.monitor_selector.pack(side = 'left', anchor='w',pady = 0, padx = 10)

        # Row for monitor selector UI elements to sit in
        self.fps_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.fps_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show user that dropdown selects the FPS
        self.current_monitor_text = customtkinter.CTkLabel(self.fps_row,width=200, height=30, text='FPS: ')
        self.current_monitor_text.pack(side = 'left', anchor="w", padx=0, pady=0) # Padding
        self.current_monitor_text.configure(state="disabled")

        # Dropdown to select the FPS
        self.fps_selector = customtkinter.CTkComboBox(self.fps_row,values=self.fps_options,
                                                          command=self.button_callback.selectfps,
                                                          state ='readonly')
        self.fps_selector.set(self.read_from_file('fps'))
        self.fps_selector.pack(side = 'left', anchor='w',pady = 0, padx = 10)

        # Row for resolution selector UI elements to sit in
        self.resolution_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.resolution_row.pack(anchor="w", fill="x", pady=(0, 12))

        self.current_resolution_text = customtkinter.CTkLabel(self.resolution_row, width=200, height=30, text='Resolution: ')
        self.current_resolution_text.pack(side='left', anchor="w", padx=0, pady=0)
        self.current_resolution_text.configure(state="disabled")

        # Dropdown to select the output resolution
        self.resolution_selector = customtkinter.CTkComboBox(self.resolution_row, values=RESOLUTION_OPTIONS,
                                                             command=self.button_callback.select_resolution,
                                                             state='readonly')
        self.resolution_selector.set(self.read_from_file('resolution') or '1080p')
        self.resolution_selector.pack(side='left', anchor='w', pady=0, padx=10)

        # Row for clip length UI elements to sit in
        self.length_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.length_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show user that dropdown selects the Clip Length
        self.current_length_text = customtkinter.CTkLabel(self.length_row, width=200, height=30, text='Clip Length: ')
        self.current_length_text.pack(side = 'left', anchor="w", padx=0, pady=0)  # Padding
        self.current_length_text.configure(state="disabled")

        # Dropdown to select the Clip Length
        self.length_selector = customtkinter.CTkComboBox(self.length_row,values=self.clip_options,
                                                          command=self.button_callback.selectcliplength,
                                                          state ='readonly')
        self.length_selector.set(self.button_callback.get_text_clip_length())
        self.length_selector.pack(side = 'left', anchor='w',pady = 0, padx = 0)


        # Row for microphone UI elements to sit in
        self.mic_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.mic_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show user that dropdown selects the Microphone
        self.current_mic_text = customtkinter.CTkLabel(self.mic_row, width=200, height=30, text='Microphone: ')
        self.current_mic_text.pack(side = 'left', anchor="w", padx=0, pady=0)  # Padding
        self.current_mic_text.configure(state="disabled")

        # Mic selector
        self.mic_selector = customtkinter.CTkComboBox(self.mic_row, values = self.get_all_mics(),
                                                      command=self.button_callback.selectmic,
                                                      state= 'readonly')
        self.mic_selector.set(self.read_from_file('mic'))
        self.mic_selector.pack(side='left', anchor = 'w', pady = 0, padx = 10)

        # Mic tickbox
        self.mic_tick_box = customtkinter.CTkCheckBox(self.mic_row, width = 60, height = 60,
                                                      state = 'normal',
                                                      command=self.button_callback.mic_status_updater,
                                                      text = '',
                                                      onvalue=1, offvalue=0,
                                                      hover = True)
        self.mic_tick_box.pack(side = 'left', anchor = 'w', pady = 0, padx = 10)

        # Defaults the mic selector to the right value
        if self.button_callback.mic_enabled == 1:
            self.mic_tick_box.select()
        else:
            self.mic_tick_box.deselect()

        # Volume slider sitting under the microphone selector
        self.mic_volume_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.mic_volume_row.pack(anchor="w", fill="x", pady=(0, 12))
        self.mic_volume_text = customtkinter.CTkLabel(self.mic_volume_row, width=200, height=20, text='Mic Volume: ')
        self.mic_volume_text.pack(side='left', anchor="w", padx=0, pady=0)
        self.mic_volume_text.configure(state="disabled")
        self.mic_volume_slider = customtkinter.CTkSlider(self.mic_volume_row, from_=0, to=1, width=200,
                                                         command=self.button_callback.set_mic_volume)
        self.mic_volume_slider.set(float(self.read_from_file('mic_volume') or 0.5))
        self.mic_volume_slider.pack(side='left', anchor='w', pady=0, padx=10)

        # Row for internal microphone UI elements to sit in
        self.internal_audio_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.internal_audio_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show user that dropdown selects the Microphone
        self.internal_audio_text = customtkinter.CTkLabel(self.internal_audio_row, width=200, height=30, text='Computer Audio: ')
        self.internal_audio_text.pack(side='left', anchor="w", padx=0, pady=0)  # Padding
        self.internal_audio_text.configure(state="disabled")

        # internal Mic selector
        self.internal_audio_selector = customtkinter.CTkComboBox(self.internal_audio_row, values=self.get_all_internal_mics(),
                                                      command=self.button_callback.select_internal_mic,
                                                      state='readonly')
        self.internal_audio_selector.set(self.button_callback.current_internal_mic)
        self.internal_audio_selector.pack(side='left', anchor='w', pady=0, padx=10)

        # Mic tickbox
        self.button_callback.internal_audio_enabled = customtkinter.IntVar()
        self.internal_audio_tick_box = customtkinter.CTkCheckBox(self.internal_audio_row, width=60, height=60,
                                                      state='normal',
                                                      command=self.button_callback.internal_audio_status_updater,
                                                      text='',
                                                      onvalue=1, offvalue=0,
                                                      hover=True)
        self.internal_audio_tick_box.pack(side='left', anchor='w', pady=0, padx=10)

        # Defaults the mic selector to the right value
        if self.button_callback.mic_enabled == 1:
            self.internal_audio_tick_box.select()
        else:
            self.internal_audio_tick_box.deselect()

        # Volume slider sitting under the computer-audio selector
        self.internal_volume_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.internal_volume_row.pack(anchor="w", fill="x", pady=(0, 12))
        self.internal_volume_text = customtkinter.CTkLabel(self.internal_volume_row, width=200, height=20, text='Computer Volume: ')
        self.internal_volume_text.pack(side='left', anchor="w", padx=0, pady=0)
        self.internal_volume_text.configure(state="disabled")
        self.internal_volume_slider = customtkinter.CTkSlider(self.internal_volume_row, from_=0, to=1, width=200,
                                                              command=self.button_callback.set_internal_volume)
        self.internal_volume_slider.set(float(self.read_from_file('internal_volume') or 0.5))
        self.internal_volume_slider.pack(side='left', anchor='w', pady=0, padx=10)

        # Row for mouse capture UI elements to sit in
        self.mouse_row = customtkinter.CTkFrame(self.left_column, fg_color="transparent")
        self.mouse_row.pack(anchor="w", fill="x", pady=(0, 12))

        # Text to show user that the tickbox captures the mouse cursor
        self.mouse_text = customtkinter.CTkLabel(self.mouse_row, width=200, height=30, text='Capture Mouse: ')
        self.mouse_text.pack(side='left', anchor="w", padx=0, pady=0)  # Padding
        self.mouse_text.configure(state="disabled")

        # Mouse tickbox
        self.mouse_tick_box = customtkinter.CTkCheckBox(self.mouse_row, width=60, height=60,
                                                      state='normal',
                                                      command=self.button_callback.mouse_status_updater,
                                                      text='',
                                                      onvalue=1, offvalue=0,
                                                      hover=True)
        self.mouse_tick_box.pack(side='left', anchor='w', pady=0, padx=10)

        # Defaults the mouse tickbox to the saved value
        if self.button_callback.mouse_enabled == 1:
            self.mouse_tick_box.select()
        else:
            self.mouse_tick_box.deselect()

        # Clip-browser button — created last and lifted so it always sits on top
        # of the other widgets, tucked into the top-right corner clear of the row
        self._clips_icon = customtkinter.CTkImage(light_image=_make_clips_icon(),
                                                  dark_image=_make_clips_icon(), size=(22, 16))
        self.clips_button = customtkinter.CTkButton(self, width=44, height=34, text='',
                                                    image=self._clips_icon,
                                                    command=self.open_clip_browser)
        self.clips_button.place(relx=1.0, rely=0.0, x=-14, y=14, anchor='ne')
        self.clips_button.lift()

        # Close to system tray
        self.system_tray_setup()
        self.protocol("WM_DELETE_WINDOW", self.hide_window)

        # Starts the audio + screen recording
        self.audio.start()
        if self.use_gpu:
            self.gpu.start()
            threading.Thread(target=self._gpu_fallback_watch, daemon=True).start()
        else:
            self.screen.start()

        # Built to sit in Startup: stay out of the way and run from the tray.
        # The window is only shown on the very first launch (so a new user can
        # set it up) or when '--show' is passed.
        if not (FIRST_RUN or '--show' in sys.argv):
            self.hide_window()

        self.loop() # The program logic

        # The mainloop that repeats the code above and keeps the window alive
        self.mainloop()

    # This is the program's main logic loop. It stays lightweight: the heavy
    # screen grabbing/encoding runs on the ScreenCapture thread, so the GUI
    # never blocks and the clip key stays responsive.
    def loop(self):
        # Run any work background threads (the tray icon) handed us
        self._pump_ui_queue()

        # Someone launched a second copy (desktop/Start Menu shortcut) — it asks
        # us to show the window rather than starting a rival instance
        if self._show_event and ctypes.windll.kernel32.WaitForSingleObject(self._show_event, 0) == 0:
            self._do_show_window()

        try:
            if keyboard.is_pressed(self.button_callback.clipping_key) and self.clip_key_pressed == False:
                self._start_clip()
                self.clip_key_pressed = True
            elif not keyboard.is_pressed(self.button_callback.clipping_key):
                self.clip_key_pressed = False
        except Exception:
            traceback.print_exc()

        # ~66Hz key polling is plenty responsive and costs the GUI almost nothing
        self.after(15,self.loop)

    def _gpu_fallback_watch(self):
        """If the GPU replay buffer can't get going within a few seconds (no
        ddagrab / hardware encoder), quietly switch to the CPU capture path so
        recording still works."""
        for _ in range(12):
            if self.gpu.available:
                return
            if not self.gpu.is_alive():
                break
            time.sleep(0.5)
        if not self.gpu.available:
            print('GPU recorder unavailable — falling back to CPU capture')
            self.use_gpu = False
            self.screen.start()

    def _start_clip(self):
        print('Clipping...')
        try:
            Popup()
        except Exception as e:
            print(f'Popup error: {e}')  # a failed popup must not stop the clip

        # GPU path: the video is already encoded in the rolling buffer, so we
        # just trim the last N seconds and mux in the matching audio.
        if self.use_gpu and self.gpu.available:
            self._start_gpu_clip()
            return

        # Grab the buffered frames (this also clears the rolling buffer)
        items, t_first, t_last = self.screen.snapshot()
        if items:
            # Grab the audio for exactly the same wall-clock window so the two
            # stay locked together
            clip_audio = self.audio.get_clip_audio(t_first, t_last)
        else:
            clip_audio = None
        self.audio.clear()

        threading.Thread(target=self.compile_clip,
                         args=(items, t_first, t_last, clip_audio),
                         daemon=True).start()

    def _start_gpu_clip(self):
        clip_length = self.gpu._clip_length()
        # Audio for the same trailing window the video clip covers
        t_end = time.time()
        audio = self.audio.get_clip_audio(t_end - clip_length, t_end)
        self.audio.clear()
        out_path = SAVE_LOCATION + str(self.button_callback.create_file_name())
        threading.Thread(target=self._gpu_save, args=(out_path, audio),
                         daemon=True).start()

    def _gpu_save(self, out_path, audio):
        audio_path = None
        try:
            if audio is not None and len(audio):
                audio_path = os.path.join(tempfile.gettempdir(),
                                          f'clip_audio_{time.time()}.wav')
                write_wav(audio_path, audio, SAMPLE_RATE)
        except Exception as e:
            print(f'Audio write error: {e}')
            audio_path = None
        try:
            ok = self.gpu.save_clip(out_path, audio_path)
            print('Clipped!' if ok else 'Clip failed')
        except Exception:
            traceback.print_exc()
        finally:
            if audio_path:
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

    def _pump_ui_queue(self):
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                traceback.print_exc()

    def _log_exception(self, exc, val, tb):
        traceback.print_exception(exc, val, tb)


# Puts all the screenshots together into a video
    def compile_clip(self, items, t_first, t_last, audio=None):
        try:
            self._compile_clip(items, t_first, t_last, audio)
        except Exception:
            traceback.print_exc()  # never let the encode thread die noisily

    def _compile_clip(self, items, t_first, t_last, audio=None):
        if not items:
            print("No frames to compile!")
            return

        # Take the real dimensions from the captured frame itself, not the
        # monitor: game-window capture produces frames sized to the window, which
        # may differ from the monitor resolution.
        try:
            first = cv2.imdecode(numpy.frombuffer(items[0][1], numpy.uint8),
                                 cv2.IMREAD_COLOR)
            height, width = first.shape[:2]
        except Exception:
            width = self.monitor['width']
            height = self.monitor['height']

        # Re-time the captured frames to a constant fps so playback matches
        # real time even when the capture rate dipped under load
        video_list, actual_fps = build_cfr_frames(items, t_first, t_last)
        print('Captured Frames: ', len(items))
        print('Output Frames: ', len(video_list))
        print('Actual FPS: ', actual_fps)

        # Write the captured audio to a temp WAV so ffmpeg can mux it in
        audio_path = None
        if audio is not None and len(audio):
            try:
                audio_path = os.path.join(tempfile.gettempdir(),
                                          f"clip_audio_{time.time()}.wav")
                write_wav(audio_path, audio, SAMPLE_RATE)
            except Exception as ae:
                print(f"Audio write error: {ae}")
                audio_path = None

        ffmpeg_cmd = [
            FFMPEG_PATH,
            '-y',  # Overwrite output file if it already exists
            '-f', 'image2pipe',  # Input format is raw pixels
            '-vcodec', 'mjpeg',
            '-pix_fmt', 'bgr0',  # MSS bgra data maps perfectly to FFmpeg's bgr0
            '-s', f"{width}x{height}",  # Tell FFmpeg the dimensions of the incoming frames
            '-r', str(actual_fps),  # Input frame rate
            '-i', '-',  # '-' tells FFmpeg to listen to the incoming RAM pipe
        ]

        # Second input: the captured audio track
        if audio_path:
            ffmpeg_cmd += ['-i', audio_path]

        # Scale the output to the chosen resolution (by height, keeping aspect).
        # -2 keeps the width even, which the encoders require.
        target_h = RESOLUTION_MAP.get(self.read_from_file('resolution'), height)
        if target_h != height:
            ffmpeg_cmd += ['-vf', f'scale=-2:{target_h}']
        else:
            # Force even dimensions — a window capture can be an odd size, which
            # yuv420p rejects. This is a no-op when the frame is already even.
            ffmpeg_cmd += ['-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']

        ffmpeg_cmd += [
            *GPU_CODEC_FLAGS,
            '-pix_fmt', 'yuv420p',  # Ensure maximum compatibility for media players
        ]

        # Encode the audio and stop at whichever stream ends first
        if audio_path:
            ffmpeg_cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']

        ffmpeg_cmd += [SAVE_LOCATION + str(self.button_callback.create_file_name())]

        process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=SUBPROCESS_FLAGS)

        try:
            for image in video_list:
                process.stdin.write(image)
            process.stdin.close()
            process.wait()
        except (BrokenPipeError, OSError) as e:
            # ffmpeg died mid-encode — don't let it take the app down with it
            print(f'Encode pipe error: {e}')
            try:
                process.kill()
            except Exception:
                pass

        # Clean up the temp audio file
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass
        print('Clipped!')


    def write_to_file(self,pointer, value):
        # print('Called Write to file')
        updated_file = []
        with open(CONFIG_PATH, 'r') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    print('Written', value, 'to', pointer)
                    line[1] = value

                updated_file.append(line) # adds the line as a new component in the list

        # re-writes the file to put in the new values accurately
        with open(CONFIG_PATH,'w',newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerows(updated_file)

    def read_from_file(self, pointer):
        with open(CONFIG_PATH,'r') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    return line[1]
            print('Unreadable Value!')
            return None
    # Microphone logic ------------------------------------------------------------------------
    # returns all the available microphone
    def get_all_mics(self):
        # This filters for inputs AND ensures it only grabs modern WASAPI devices
        wasapi_mics = [
            d['name'] for d in sd.query_devices()
            if d['max_input_channels'] > 0 and sd.query_hostapis(d['hostapi'])['name'] == 'Windows WASAPI'
        ]
        return wasapi_mics

    def get_all_internal_mics(self):
        # This filters for inputs AND ensures it only grabs modern WASAPI devices
        wasapi_mics = [
            d['name'] for d in sd.query_devices()
            if d['max_input_channels'] == 0 and sd.query_hostapis(d['hostapi'])['name'] == 'Windows WASAPI'
        ]
        return wasapi_mics


    # Clip browser ------------------------------------------------------------------------
    def open_clip_browser(self):
        if self.clip_browser is not None and self.clip_browser.winfo_exists():
            self.clip_browser.deiconify()
            self.clip_browser.lift()
            self.clip_browser.focus()
        else:
            self.clip_browser = ClipBrowser(self)

    # Tray Logic ------------------------------------------------------------------------
    # The tray menu callbacks fire on pystray's own thread, so they must NOT
    # touch Tk directly — they hand the work to the main thread via the queue.

    # Brings the window back up (tray "Open")
    def show_window(self, *args):
        self._ui_queue.put(self._do_show_window)

    def _do_show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    # Hides the window (called on the main thread: window close button / startup)
    def hide_window(self):
        self.withdraw()

    # Shuts down the app (tray "Quit")
    def quit_app(self, *args):
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:
            pass
        self._ui_queue.put(self._do_quit)

    def _do_quit(self):
        try:
            self.screen.stop()
            self.gpu.stop()   # kills the ddagrab/NVENC ffmpeg so it isn't orphaned
            self.audio.stop()
        except Exception:
            pass
        # quit() unwinds mainloop cleanly; the daemon threads exit with the
        # process. (destroy() here would error as the loop keeps ticking.)
        self.quit()

    # Initialises the system tray logic
    def system_tray_setup(self):
        # Creates the menu and icon
        menu = pystray.Menu(pystray.MenuItem('Open',self.show_window),
                            pystray.MenuItem('Quit', self.quit_app))
        self._tray_icon = pystray.Icon('Clipping Software',TRAY_ICON,
                                       'Clipping Software', menu)

        # Runs it in a thread so it doesn't stop the rest of the program
        tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
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
    if already_running():
        # Don't start a rival copy — just surface the one that's already there
        signal_existing_instance()
        sys.exit(0)
    program = MainProgram()
