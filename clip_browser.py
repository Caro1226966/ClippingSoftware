from config import *
import shutil
import struct
import win32clipboard

# The folder all the clips get saved into
CLIP_FOLDER = os.path.dirname(SAVE_LOCATION)

# Cache of tile thumbnails/metadata so re-opening the browser is instant
_INFO_CACHE = {}


# Helpers ------------------------------------------------------------------------
def list_clips():
    """All mp4s in the clipping folder, newest first."""
    if not os.path.isdir(CLIP_FOLDER):
        return []
    files = [os.path.join(CLIP_FOLDER, f) for f in os.listdir(CLIP_FOLDER)
             if f.lower().endswith('.mp4')]
    files.sort(key=os.path.getmtime, reverse=True)
    return files


def fmt_time(seconds):
    seconds = max(int(seconds), 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def human_size(nbytes):
    if nbytes >= 1000 * 1000:
        return f"{nbytes / (1000 * 1000):.1f} MB"
    return f"{nbytes / 1000:.0f} KB"


def copy_files_to_clipboard(paths):
    """Puts real file references (CF_HDROP) on the clipboard so the user can
    paste them straight into Discord / Explorer with Ctrl+V."""
    # DROPFILES header: pFiles offset, pt.x, pt.y, fNC, fWide(=unicode)
    data = struct.pack('<iiiii', 20, 0, 0, 0, 1) + \
           ('\0'.join(paths) + '\0\0').encode('utf-16-le')
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, data)
    finally:
        win32clipboard.CloseClipboard()


def sanitize_name(name):
    name = name.strip()
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, '')
    return name


def get_clip_info(path, thumb_w, thumb_h):
    """Returns {'thumb': PIL image, 'duration': s, 'size': bytes} for a clip."""
    try:
        key = (path, os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        key = (path, 0, 0)
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]

    duration = 0.0
    pil = Image.new('RGB', (thumb_w, thumb_h), '#101010')
    try:
        cap = cv2.VideoCapture(path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if fps > 0:
                duration = count / fps
            # Grab a frame a little way in so the thumb isn't a black screen
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(15, max(count - 1, 0)))
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                scale = min(thumb_w / w, thumb_h / h)
                nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
                small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                # Letterbox onto the dark background
                pil.paste(Image.fromarray(rgb), ((thumb_w - nw) // 2, (thumb_h - nh) // 2))
        cap.release()
    except Exception as e:
        print(f'Thumbnail error for {path}: {e}')

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    info = {'thumb': pil, 'duration': duration, 'size': size}
    _INFO_CACHE[key] = info
    return info


# The playback engine ------------------------------------------------------------
class ClipPlayer:
    """Plays one clip: video frames via OpenCV, audio via sounddevice.

    The audio track is the master clock so the picture always stays in
    sync; clips with no audio fall back to a wall clock."""

    def __init__(self, path):
        self.path = path
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError('Could not open the clip')
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        if not self.fps or self.fps <= 1 or self.fps > 480:
            self.fps = 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.duration = max(self.frame_count / self.fps, 0.04)

        self.sr = 44100
        self.audio = None
        self._cursor = 0          # audio sample position
        self._stream = None
        self.playing = False
        self._base = 0.0          # wall-clock fallback position
        self._t0 = None
        self._next_idx = 0        # next frame the decoder will hand us
        self._last_idx = -1
        self._last_frame = None

    def load_audio(self):
        """Decodes the clip's audio track into memory (16-bit stereo)."""
        try:
            r = subprocess.run(
                [FFMPEG_PATH, '-v', 'error', '-i', self.path, '-vn',
                 '-f', 's16le', '-acodec', 'pcm_s16le', '-ac', '2', '-ar', str(self.sr), '-'],
                capture_output=True, creationflags=SUBPROCESS_FLAGS)
            if r.stdout and len(r.stdout) >= 4:
                self.audio = numpy.frombuffer(r.stdout, numpy.int16).reshape(-1, 2)
        except Exception as e:
            print(f'Audio load error: {e}')

    @property
    def position(self):
        if self.audio is not None:
            return self._cursor / self.sr
        if self.playing and self._t0 is not None:
            return self._base + (time.time() - self._t0)
        return self._base

    def seek(self, t):
        t = min(max(t, 0.0), self.duration)
        if self.audio is not None:
            self._cursor = min(int(t * self.sr), len(self.audio))
        self._base = t
        self._t0 = time.time()

    def play(self):
        if self.playing:
            return
        if self.position >= self.duration - 0.05:
            self.seek(0.0)
        self._base = self.position
        self._t0 = time.time()
        self.playing = True
        if self.audio is not None:
            try:
                self._stream = sd.OutputStream(samplerate=self.sr, channels=2,
                                               dtype='int16', callback=self._cb)
                self._stream.start()
            except Exception as e:
                print(f'Audio playback error: {e}')
                self.audio = None  # fall back to silent wall-clock playback

    def pause(self):
        if not self.playing:
            return
        self._base = self.position
        self.playing = False
        if self._stream:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _cb(self, outdata, frames, time_info, status):
        start = self._cursor
        chunk = self.audio[start:start + frames]
        n = len(chunk)
        outdata[:n] = chunk
        if n < frames:
            outdata[n:] = 0
            self._cursor = len(self.audio)
            raise sd.CallbackStop()
        self._cursor = start + frames

    def get_frame(self, t):
        """Returns the BGR frame for time t, stepping the decoder efficiently."""
        idx = min(int(t * self.fps), max(self.frame_count - 1, 0))
        if idx == self._last_idx and self._last_frame is not None:
            return self._last_frame
        # Jump if we're going backwards or far forwards, otherwise step
        if idx < self._next_idx or idx > self._next_idx + 12:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            self._next_idx = idx
        while self._next_idx < idx:
            self.cap.grab()
            self._next_idx += 1
        ok, frame = self.cap.read()
        if not ok:
            return self._last_frame
        self._next_idx = idx + 1
        self._last_idx = idx
        self._last_frame = frame
        return frame

    def close(self):
        self.pause()
        try:
            self.cap.release()
        except Exception:
            pass
        self.audio = None


# The clip browser window --------------------------------------------------------
class ClipBrowser(customtkinter.CTkToplevel):
    COLS = 3
    THUMB_W, THUMB_H = 280, 158
    VID_W, VID_H = 920, 470
    SIZES = ['10 MB', '20 MB', '50 MB', '100 MB', 'Original']

    def __init__(self, master):
        super().__init__(master)
        self.title('Clips')
        self.geometry('960x640')
        self.resizable(False, False)
        # Pop above the main window, then behave like a normal window
        self.attributes('-topmost', True)
        self.after(300, lambda: self.attributes('-topmost', False))

        self._player = None
        self._page = None
        self._current_path = None       # clip currently open in the viewer
        self._scrubbing = False
        self._suppress_slider = False
        self._load_token = 0
        self._pending_load = None        # result handed from the loader thread to _tick
        self._tile_imgs = []             # keep CTkImage refs alive
        self._frame_img = None
        self._known_files = []
        self._overlay = None

        # Share/compress state (lives on the browser now that it's a page)
        self._share_src = None
        self._share_return = 'grid'
        self._share_tmpdir = None
        self._share_result = None
        self._share_proc = None
        self._share_cancel = threading.Event()
        self._share_state = 'idle'       # idle | working | done | error
        self._share_progress_val = 0.0
        self._share_status_text = ''

        self._grid_page = customtkinter.CTkFrame(self, fg_color='transparent')
        self._viewer_page = customtkinter.CTkFrame(self, fg_color='transparent')
        self._share_page = customtkinter.CTkFrame(self, fg_color='transparent')
        self._build_grid_page()
        self._build_viewer_page()
        self._build_share_page()
        self._show_page('grid')
        self._refresh_grid()

        self.bind('<FocusIn>', self._on_focus)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.after(33, self._tick)

    # Page plumbing ------------------------------------------------------------
    def _show_page(self, page):
        self._page = page
        for name, frame in (('grid', self._grid_page), ('viewer', self._viewer_page),
                            ('share', self._share_page)):
            if name == page:
                frame.pack(fill='both', expand=True)
            else:
                frame.pack_forget()

    # Modal overlays (rename / delete / errors) stay inside the one window -----
    def _open_overlay(self):
        self._close_overlay()
        self._overlay = customtkinter.CTkFrame(self, fg_color=('gray85', 'gray8'))
        self._overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._overlay.bind('<Button-1>', lambda e: 'break')
        box = customtkinter.CTkFrame(self._overlay, corner_radius=14, border_width=1,
                                     border_color='#444444')
        box.place(relx=0.5, rely=0.5, anchor='center')
        return box

    def _close_overlay(self):
        if self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None

    def _show_message(self, title, msg):
        box = self._open_overlay()
        customtkinter.CTkLabel(box, text=title,
                               font=customtkinter.CTkFont(size=15, weight='bold')).pack(padx=36, pady=(22, 6))
        customtkinter.CTkLabel(box, text=msg, wraplength=320).pack(padx=36, pady=6)
        customtkinter.CTkButton(box, text='OK', width=90,
                                command=self._close_overlay).pack(padx=36, pady=(6, 20))

    # Grid page ----------------------------------------------------------------
    def _build_grid_page(self):
        header = customtkinter.CTkFrame(self._grid_page, fg_color='transparent')
        header.pack(fill='x', padx=16, pady=(12, 0))
        customtkinter.CTkLabel(header, text='Your Clips',
                               font=customtkinter.CTkFont(size=18, weight='bold')).pack(side='left')
        self._count_label = customtkinter.CTkLabel(header, text='', text_color='#8a8a8a')
        self._count_label.pack(side='left', padx=12)

        self._scroll = customtkinter.CTkScrollableFrame(self._grid_page, fg_color='transparent')
        self._scroll.pack(fill='both', expand=True, padx=8, pady=8)

    def _refresh_grid(self):
        for child in self._scroll.winfo_children():
            child.destroy()
        self._tile_imgs.clear()

        files = list_clips()
        self._known_files = files
        self._count_label.configure(text=f'{len(files)} clip{"s" if len(files) != 1 else ""}')

        if not files:
            customtkinter.CTkLabel(self._scroll, text='No clips yet — go make some!',
                                   text_color='#8a8a8a').pack(pady=40)
            return

        for i, path in enumerate(files):
            self._make_tile(path, i)

    def _make_tile(self, path, i):
        info = get_clip_info(path, self.THUMB_W, self.THUMB_H)
        stem = os.path.splitext(os.path.basename(path))[0]

        tile = customtkinter.CTkFrame(self._scroll, corner_radius=10, fg_color='#2b2b2b')
        tile.grid(row=i // self.COLS, column=i % self.COLS, padx=8, pady=8, sticky='n')

        img = customtkinter.CTkImage(light_image=info['thumb'], dark_image=info['thumb'],
                                     size=(self.THUMB_W, self.THUMB_H))
        self._tile_imgs.append(img)
        thumb = customtkinter.CTkLabel(tile, image=img, text='')
        thumb.grid(row=0, column=0, padx=6, pady=(6, 0))

        name = customtkinter.CTkLabel(tile, text=stem, anchor='w', width=self.THUMB_W)
        name.grid(row=1, column=0, sticky='w', padx=10, pady=(4, 0))
        meta = customtkinter.CTkLabel(tile, anchor='w', width=self.THUMB_W, text_color='#8a8a8a',
                                      font=customtkinter.CTkFont(size=11),
                                      text=f"{fmt_time(info['duration'])}  ·  {human_size(info['size'])}")
        meta.grid(row=2, column=0, sticky='w', padx=10, pady=(0, 8))

        # Bigger 3-dots options button, bottom-right, shown on hover
        dots = customtkinter.CTkButton(tile, text='⋮', width=40, height=40, corner_radius=8,
                                       font=customtkinter.CTkFont(size=22, weight='bold'),
                                       fg_color='#141414', hover_color='#1f538d',
                                       command=lambda p=path: self._open_menu(p))

        for w in (tile, thumb, name, meta):
            w.bind('<Button-1>', lambda e, p=path: self._open_viewer(p))
        tile.bind('<Enter>', lambda e, d=dots: d.place(relx=1.0, rely=1.0, x=-12, y=-12, anchor='se'))
        tile.bind('<Leave>', lambda e, t=tile, d=dots: self._maybe_hide_dots(e, t, d))

    def _maybe_hide_dots(self, event, tile, dots):
        # Moving onto a child fires <Leave> on the tile; only hide when the
        # pointer has genuinely left the tile's rectangle
        under = self.winfo_containing(event.x_root, event.y_root)
        if under is not None and str(under).startswith(str(tile)):
            return
        dots.place_forget()

    def _open_menu(self, path):
        menu = tk.Menu(self, tearoff=0, bg='#2b2b2b', fg='#eeeeee', bd=0,
                       activebackground='#1f538d', activeforeground='white')
        menu.add_command(label='   Rename   ',
                         command=lambda: self._rename_clip(path, lambda np: self._refresh_grid()))
        menu.add_command(label='   Share   ', command=lambda: self._open_share(path, 'grid'))
        menu.add_separator()
        menu.add_command(label='   Delete   ',
                         command=lambda: self._delete_clip(path, self._refresh_grid))
        try:
            x, y = self.winfo_pointerxy()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # Rename / delete (as in-window overlays) ----------------------------------
    def _rename_clip(self, path, after):
        box = self._open_overlay()
        stem = os.path.splitext(os.path.basename(path))[0]
        customtkinter.CTkLabel(box, text='Rename clip',
                               font=customtkinter.CTkFont(size=15, weight='bold')).pack(padx=36, pady=(22, 8))
        entry = customtkinter.CTkEntry(box, width=300)
        entry.insert(0, stem)
        entry.pack(padx=36, pady=4)
        entry.focus()
        entry.select_range(0, 'end')
        err = customtkinter.CTkLabel(box, text='', text_color='#e06c6c',
                                     font=customtkinter.CTkFont(size=11))
        err.pack(padx=36)

        def save():
            name = sanitize_name(entry.get())
            if not name:
                err.configure(text='Please enter a name.')
                return
            new_path = os.path.join(CLIP_FOLDER, name + '.mp4')
            if os.path.abspath(new_path) == os.path.abspath(path):
                self._close_overlay()
                return
            if os.path.exists(new_path):
                err.configure(text='A clip with that name already exists.')
                return
            # Release the file handle if this clip is open in the viewer
            if self._player is not None and getattr(self._player, 'path', None) == path:
                self._close_player()
            try:
                os.rename(path, new_path)
            except OSError as e:
                err.configure(text=f'Could not rename: {e}')
                return
            self._close_overlay()
            after(new_path)

        row = customtkinter.CTkFrame(box, fg_color='transparent')
        row.pack(padx=36, pady=(10, 20))
        customtkinter.CTkButton(row, text='Cancel', width=100, fg_color='#3a3a3a',
                                hover_color='#4a4a4a', command=self._close_overlay).pack(side='left', padx=6)
        customtkinter.CTkButton(row, text='Save', width=100, command=save).pack(side='left', padx=6)
        entry.bind('<Return>', lambda e: save())

    def _delete_clip(self, path, after):
        box = self._open_overlay()
        stem = os.path.splitext(os.path.basename(path))[0]
        customtkinter.CTkLabel(box, text='Delete clip',
                               font=customtkinter.CTkFont(size=15, weight='bold')).pack(padx=40, pady=(22, 6))
        customtkinter.CTkLabel(box, text=f'Delete "{stem}" forever?',
                               wraplength=320).pack(padx=40, pady=4)
        err = customtkinter.CTkLabel(box, text='', text_color='#e06c6c',
                                     font=customtkinter.CTkFont(size=11))
        err.pack(padx=40)

        def do_delete():
            if self._player is not None and getattr(self._player, 'path', None) == path:
                self._close_player()
            try:
                os.remove(path)
            except OSError as e:
                err.configure(text=f'Could not delete: {e}')
                return
            self._close_overlay()
            after()

        row = customtkinter.CTkFrame(box, fg_color='transparent')
        row.pack(padx=40, pady=(10, 20))
        customtkinter.CTkButton(row, text='Cancel', width=100, fg_color='#3a3a3a',
                                hover_color='#4a4a4a', command=self._close_overlay).pack(side='left', padx=6)
        customtkinter.CTkButton(row, text='Delete', width=100, fg_color='#a83232',
                                hover_color='#c23a3a', command=do_delete).pack(side='left', padx=6)

    def _on_focus(self, event):
        # Pick up clips that were created/renamed while the window was unfocused
        if self._page == 'grid' and list_clips() != self._known_files:
            self._refresh_grid()

    # Viewer page ---------------------------------------------------------------
    def _build_viewer_page(self):
        header = customtkinter.CTkFrame(self._viewer_page, fg_color='transparent')
        header.pack(fill='x', padx=16, pady=(10, 4))
        customtkinter.CTkButton(header, text='←  Back', width=80,
                                command=self._back_to_grid).pack(side='left')
        self._viewer_title = customtkinter.CTkLabel(header, text='',
                                                    font=customtkinter.CTkFont(size=15, weight='bold'))
        self._viewer_title.pack(side='left', padx=14)

        # Share / Rename / Delete actions live on the viewer too
        customtkinter.CTkButton(header, text='Delete', width=72, fg_color='#a83232',
                                hover_color='#c23a3a', command=self._viewer_delete).pack(side='right', padx=(6, 0))
        customtkinter.CTkButton(header, text='Rename', width=72,
                                command=self._viewer_rename).pack(side='right', padx=6)
        customtkinter.CTkButton(header, text='Share', width=72,
                                command=self._viewer_share).pack(side='right', padx=6)

        self._video_label = customtkinter.CTkLabel(self._viewer_page, text='', fg_color='#000000',
                                                   width=self.VID_W, height=self.VID_H)
        self._video_label.pack(padx=16, pady=4)
        # One persistent CTkImage is reused for every frame. Creating a fresh
        # CTkImage per frame churns Tk PhotoImages and can crash the redraw
        # ("image doesn't exist"), so we only ever reconfigure this one. We use
        # only light_image (CTkImage falls back to it in dark mode) so that
        # resizing it never trips CTkImage's light/dark size-match check.
        self._black_pil = Image.new('RGB', (self.VID_W, self.VID_H), '#000000')
        self._frame_img = customtkinter.CTkImage(light_image=self._black_pil,
                                                 size=(self.VID_W, self.VID_H))
        self._video_label.configure(image=self._frame_img)

        controls = customtkinter.CTkFrame(self._viewer_page, fg_color='transparent')
        controls.pack(fill='x', padx=20, pady=(2, 10))
        self._play_btn = customtkinter.CTkButton(controls, text='▶', width=44,
                                                 command=self._toggle_play)
        self._play_btn.pack(side='left')
        self._time_cur = customtkinter.CTkLabel(controls, text='0:00', width=44)
        self._time_cur.pack(side='left', padx=(10, 4))
        self._slider = customtkinter.CTkSlider(controls, from_=0, to=1, command=self._on_scrub)
        self._slider.set(0)
        self._slider.pack(side='left', fill='x', expand=True, padx=6)
        self._slider.bind('<Button-1>', lambda e: self._set_scrubbing(True), add='+')
        self._slider.bind('<ButtonRelease-1>', lambda e: self._set_scrubbing(False), add='+')
        self._time_total = customtkinter.CTkLabel(controls, text='0:00', width=44)
        self._time_total.pack(side='left', padx=(4, 0))

    def _open_viewer(self, path):
        self._close_player()
        self._current_path = path
        self._load_token += 1
        token = self._load_token
        self._viewer_title.configure(text=os.path.splitext(os.path.basename(path))[0])
        # Blank to black while the clip loads (reuse the persistent image)
        self._frame_img.configure(light_image=self._black_pil, size=(self.VID_W, self.VID_H))
        self._play_btn.configure(text='▶')
        self._slider.set(0)
        self._time_cur.configure(text='0:00')
        self._show_page('viewer')
        threading.Thread(target=self._load_player, args=(path, token), daemon=True).start()

    def _load_player(self, path, token):
        # Runs on a worker thread: no tkinter calls allowed here. The result
        # is parked in _pending_load and picked up by the _tick loop.
        try:
            player = ClipPlayer(path)
            player.load_audio()
            self._pending_load = ('player', player, token)
        except Exception as e:
            self._pending_load = ('error', str(e), token)

    def _load_failed(self, token, msg):
        if token != self._load_token or not self.winfo_exists():
            return
        self._back_to_grid()
        self._show_message('Open clip', f'Could not open the clip:\n{msg}')

    def _player_ready(self, player, token):
        if token != self._load_token or not self.winfo_exists():
            player.close()
            return
        self._player = player
        self._slider.configure(to=max(player.duration, 0.05))
        self._time_total.configure(text=fmt_time(player.duration))
        self._show_frame(0.0)
        player.play()
        self._play_btn.configure(text='❚❚')

    def _toggle_play(self):
        p = self._player
        if not p:
            return
        if p.playing:
            p.pause()
            self._play_btn.configure(text='▶')
        else:
            p.play()
            self._play_btn.configure(text='❚❚')

    def _set_scrubbing(self, on):
        self._scrubbing = on

    def _on_scrub(self, value):
        if self._suppress_slider or not self._player:
            return
        t = float(value)
        self._player.seek(t)
        self._show_frame(t)
        self._time_cur.configure(text=fmt_time(t))

    def _set_slider(self, value):
        self._suppress_slider = True
        self._slider.set(value)
        self._suppress_slider = False

    def _show_frame(self, t):
        p = self._player
        if not p:
            return
        frame = p.get_frame(t)
        if frame is None:
            return
        h, w = frame.shape[:2]
        scale = min(self.VID_W / w, self.VID_H / h)
        nw, nh = max(int(w * scale), 1), max(int(h * scale), 1)
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        # Reuse the one persistent CTkImage instead of creating a new one
        self._frame_img.configure(light_image=pil, size=(nw, nh))

    def _viewer_share(self):
        if self._player:
            self._player.pause()
            self._play_btn.configure(text='▶')
        self._open_share(self._current_path, 'viewer')

    def _viewer_rename(self):
        self._rename_clip(self._current_path, lambda np: self._open_viewer(np))

    def _viewer_delete(self):
        self._delete_clip(self._current_path, self._back_to_grid)

    def _close_player(self):
        if self._player:
            self._player.close()
            self._player = None

    def _back_to_grid(self):
        self._load_token += 1
        self._close_player()
        self._show_page('grid')
        if list_clips() != self._known_files:
            self._refresh_grid()

    # Share page ---------------------------------------------------------------
    def _build_share_page(self):
        header = customtkinter.CTkFrame(self._share_page, fg_color='transparent')
        header.pack(fill='x', padx=16, pady=(10, 4))
        customtkinter.CTkButton(header, text='←  Back', width=80,
                                command=self._leave_share).pack(side='left')
        self._share_title = customtkinter.CTkLabel(header, text='',
                                                   font=customtkinter.CTkFont(size=15, weight='bold'))
        self._share_title.pack(side='left', padx=14)

        body = customtkinter.CTkFrame(self._share_page, fg_color='transparent')
        body.pack(expand=True)

        customtkinter.CTkLabel(body, text='Compress to:', text_color='#8a8a8a').pack(pady=(30, 6))
        self._share_seg = customtkinter.CTkSegmentedButton(body, values=self.SIZES)
        self._share_seg.set('10 MB')
        self._share_seg.pack(pady=6)

        self._share_prepare_btn = customtkinter.CTkButton(body, text='Prepare file',
                                                          command=self._share_prepare)
        self._share_prepare_btn.pack(pady=(10, 8))

        self._share_progress = customtkinter.CTkProgressBar(body, width=380)
        self._share_progress.set(0)
        self._share_progress.pack(pady=(6, 2))
        self._share_status = customtkinter.CTkLabel(body, text='Pick a size and press Prepare',
                                                    text_color='#8a8a8a')
        self._share_status.pack()

        self._share_copy_btn = customtkinter.CTkButton(body, text='📋  Copy file to clipboard',
                                                       width=280, height=52, state='disabled',
                                                       font=customtkinter.CTkFont(size=15, weight='bold'),
                                                       fg_color='#3a3a3a', command=self._share_copy)
        self._share_copy_btn.pack(pady=(24, 6))
        customtkinter.CTkLabel(body, text='then paste it into Discord with Ctrl + V',
                               text_color='#8a8a8a', font=customtkinter.CTkFont(size=11)).pack()

    def _open_share(self, path, return_page):
        if not path:
            return
        # Abandon any previous compression and its temp file
        self._share_cancel.set()
        self._kill_share_proc()
        self._cleanup_share_tmp()

        self._share_src = path
        self._share_return = return_page
        self._share_result = None
        self._share_state = 'idle'
        self._share_progress_val = 0.0
        stem = os.path.splitext(os.path.basename(path))[0]
        self._share_title.configure(text=f'Share "{stem}"')
        self._share_seg.set('10 MB')
        self._share_progress.set(0)
        self._share_status.configure(text='Pick a size and press Prepare')
        self._share_prepare_btn.configure(state='normal')
        self._share_copy_btn.configure(state='disabled', fg_color='#3a3a3a')
        self._show_page('share')

    def _leave_share(self):
        # Leaving the compression menu = bin the compressed copy
        self._share_cancel.set()
        self._kill_share_proc()
        self._share_state = 'idle'
        self._cleanup_share_tmp()
        self._show_page(self._share_return)
        if self._share_return == 'grid' and list_clips() != self._known_files:
            self._refresh_grid()

    def _share_prepare(self):
        if self._share_state == 'working':
            return
        self._share_result = None
        self._share_copy_btn.configure(state='disabled', fg_color='#3a3a3a')
        self._cleanup_share_tmp()

        choice = self._share_seg.get()
        if choice == 'Original':
            self._share_set_ready(self._share_src,
                                  f'Original ready — {human_size(os.path.getsize(self._share_src))}')
            return

        mb = int(choice.split()[0])
        self._share_state = 'working'
        self._share_cancel.clear()
        self._share_progress.set(0)
        self._share_prepare_btn.configure(state='disabled')
        self._share_status.configure(text='Compressing…')
        threading.Thread(target=self._share_worker, args=(mb, self._share_src), daemon=True).start()

    def _share_set_ready(self, path, status):
        self._share_result = path
        self._share_state = 'done'
        self._share_progress.set(1)
        self._share_prepare_btn.configure(state='normal')
        self._share_status.configure(text=status)
        self._share_copy_btn.configure(state='normal', fg_color='#1f538d')

    def _share_copy(self):
        if not self._share_result:
            return
        try:
            copy_files_to_clipboard([self._share_result])
            self._share_status.configure(text='Copied! Paste into Discord now (Ctrl + V) before going back')
        except Exception as e:
            self._share_status.configure(text=f'Clipboard error: {e}')

    def _share_worker(self, mb, src):
        try:
            if self._share_cancel.is_set():
                return
            target_bytes = int(mb * 1000 * 1000 * 0.95)  # a little headroom under the limit
            if os.path.getsize(src) <= target_bytes:
                self._share_status_text = f'Already under {mb} MB — using the original'
                self._share_result = src
                self._share_state = 'done'
                return

            # Work out the clip length so we can hit the target size
            cap = cv2.VideoCapture(src)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            cap.release()
            duration = count / fps if fps > 0 else 0
            if duration <= 0:
                raise RuntimeError('could not read clip duration')

            audio_br = 96000 if mb <= 10 else 128000
            video_br = max(int(target_bytes * 8 / duration) - audio_br, 80000)

            if self._share_cancel.is_set():
                return
            self._share_tmpdir = tempfile.mkdtemp(prefix='clipshare_')
            log = os.path.join(self._share_tmpdir, 'ff2pass')
            stem = os.path.splitext(os.path.basename(src))[0]
            out = os.path.join(self._share_tmpdir, f'{stem}_{mb}MB.mp4')

            # Two-pass x264 = reliable size targeting
            common = [FFMPEG_PATH, '-y', '-v', 'error', '-progress', 'pipe:1',
                      '-i', src, '-c:v', 'libx264', '-preset', 'veryfast',
                      '-b:v', str(video_br), '-passlogfile', log]
            pass1 = common + ['-pass', '1', '-an', '-f', 'null', 'NUL']
            pass2 = common + ['-pass', '2', '-c:a', 'aac', '-b:a', str(audio_br),
                              '-movflags', '+faststart', out]
            for i, cmd in enumerate((pass1, pass2)):
                self._share_run_ffmpeg(cmd, duration, i)

            self._share_status_text = f'Ready — {human_size(os.path.getsize(out))}. Copy it below ⬇'
            self._share_result = out
            self._share_state = 'done'
        except _Cancelled:
            self._cleanup_share_tmp()
            self._share_state = 'idle'
        except Exception as e:
            print(f'Compression error: {e}')
            self._share_status_text = f'Compression failed: {e}'
            self._share_state = 'error'

    def _share_run_ffmpeg(self, cmd, duration, pass_idx):
        self._share_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             stdin=subprocess.DEVNULL, text=True,
                                             creationflags=SUBPROCESS_FLAGS)
        for line in self._share_proc.stdout:
            if self._share_cancel.is_set():
                self._share_proc.kill()
                self._share_proc.wait()
                raise _Cancelled()
            line = line.strip()
            if line.startswith('out_time_ms=') or line.startswith('out_time_us='):
                try:
                    t = int(line.split('=')[1]) / 1_000_000
                    self._share_progress_val = min((pass_idx + min(t / duration, 1.0)) / 2, 1.0)
                except ValueError:
                    pass
        self._share_proc.wait()
        if self._share_cancel.is_set():
            raise _Cancelled()
        if self._share_proc.returncode != 0:
            raise RuntimeError(f'ffmpeg exited with code {self._share_proc.returncode}')

    def _kill_share_proc(self):
        try:
            if self._share_proc and self._share_proc.poll() is None:
                self._share_proc.kill()
        except Exception:
            pass

    def _cleanup_share_tmp(self):
        d = self._share_tmpdir
        self._share_tmpdir = None
        if not d:
            return

        def rm(attempt=0):
            try:
                shutil.rmtree(d)
            except OSError:
                if attempt < 6 and self.winfo_exists():
                    self.after(400, lambda: rm(attempt + 1))
        rm()

    # Main loop ----------------------------------------------------------------
    def _tick(self):
        if not self.winfo_exists():
            return

        # Collect a finished (or failed) clip load from the worker thread
        pending = self._pending_load
        if pending is not None:
            self._pending_load = None
            kind, payload, token = pending
            if kind == 'player':
                self._player_ready(payload, token)
            else:
                self._load_failed(token, payload)

        # Advance the viewer
        p = self._player
        if self._page == 'viewer' and p and p.playing and not self._scrubbing:
            pos = p.position
            if pos >= p.duration:
                p.pause()
                pos = p.duration
                self._play_btn.configure(text='▶')
            self._set_slider(pos)
            self._show_frame(pos)
            self._time_cur.configure(text=fmt_time(pos))

        # Reflect the compression worker's state onto the share page
        if self._share_state == 'working':
            self._share_progress.set(self._share_progress_val)
        elif self._share_state == 'done' and self._share_result and \
                self._share_copy_btn.cget('state') == 'disabled':
            self._share_set_ready(self._share_result, self._share_status_text)
        elif self._share_state == 'error':
            self._share_state = 'idle'
            self._share_prepare_btn.configure(state='normal')
            self._share_progress.set(0)
            self._share_status.configure(text=self._share_status_text)

        self.after(33, self._tick)

    def _on_close(self):
        self._load_token += 1
        self._share_cancel.set()
        self._kill_share_proc()
        self._close_player()
        tmpdir = self._share_tmpdir
        self._share_tmpdir = None
        self.destroy()
        # Bin the compressed temp file after the window is gone
        if tmpdir:
            def late_cleanup(attempt=0):
                try:
                    shutil.rmtree(tmpdir)
                except OSError:
                    if attempt < 6:
                        self.master.after(500, lambda: late_cleanup(attempt + 1))
            self.master.after(300, late_cleanup)


class _Cancelled(Exception):
    pass
