from config import *

class Callbacks:
    def __init__(self, main):
        self.main = main # Main Program link

        # Clipping key flags
        self.clipping_key = str(main.read_from_file('clip_key'))
        self.setbutton_pressed = False

        # Monitor selector flags
        self.monitor = int(main.read_from_file('monitor'))

        # FPS
        self.fps = int(main.read_from_file('fps'))

        # Clip Length
        self.clip_length = int(main.read_from_file('clip_length'))

        # Output
        self.file_name = self.create_file_name()

        # Mic
        self.current_mic = str(self.main.read_from_file('mic'))
        self.audio_mic = str(self.getaudio(self.current_mic))
        self.mic_enabled = int(self.main.read_from_file('mic_enabled'))

        # Internal Mic
        self.current_internal_mic = str(self.main.read_from_file('internal_mic'))
        self.audio_internal = str(self.getaudio(self.current_internal_mic))
        self.internal_mic_enabled = int(self.main.read_from_file('internal_mic_enabled'))

        # Mouse capture
        self.mouse_enabled = int(self.main.read_from_file('mouse_enabled') or 0)


    # Lets the user set the button. Changes text and
    def setbutton(self):
        if not self.setbutton_pressed:
            self.setbutton_pressed = True
            self.clipping_key = None # Resets the clipping key

            # Lets the user set their key
            while self.clipping_key is None:
                self.clipping_key = keyboard.read_key()

            # Changes the text to new values
            self.main.current_button_text.configure(state="normal")  # configure textbox to be not read-only
            self.main.current_button_text.delete("0.0", "end")  # delete all text
            self.main.current_button_text.insert(index=0.0,text=('Current Key: '+str(self.clipping_key))) # Insert new values
            self.main.current_button_text.configure(state="disabled")  # configure textbox to be read-only

            # Update the defaults.csv file
            self.main.write_to_file(value=self.clipping_key,pointer='clip_key')

            # Prints it just to be sure
            print(self.clipping_key)

            self.setbutton_pressed = False
            self.main.clip_key_pressed = True

# Selects the monitor
    def selectmonitor(self, choice):
        self.monitor = int(choice[-1])

        print(self.monitor)
        self.main.write_to_file('monitor',str(self.monitor))

# Creates the file name based on the time it was taken
    @staticmethod
    def create_file_name():
        return str(time.time()) + '.mp4' # Makes the file name based on the time

# The method for the user to select the fps
    def selectfps(self, choice):
        self.fps = int(choice)
        self.main.write_to_file(pointer ='fps', value=self.fps)

        self.main.screen.clear() # Makes sure it doesn't cock up the clip's speed
        self.main.audio.clear()

# The method for the user to select the clip length
    def selectcliplength(self, choice):
        # Puts the correct value for the clip length
        if choice == '10s':
            self.clip_length = 10
        elif choice == '30s':
            self.clip_length = 30
        elif choice == '1m':
            self.clip_length = 60
        elif choice == '2m':
            self.clip_length = 120
        elif choice == '5m':
            self.clip_length = 300
        elif choice == '10m':
            self.clip_length = 600

        self.main.write_to_file(pointer ='clip_length', value=self.clip_length) # Writes clip length to the file
        self.main.screen.clear()  # Makes sure it doesn't cock up the clip's speed
        self.main.audio.restart()  # Rebuild the audio buffer to match the new clip length

    # Converts the seconds to mins (if needed)
    def get_text_clip_length(self):
        choice = self.main.read_from_file('clip_length')
        if choice == '10':
            return '10s'
        elif choice == '30':
            return '30s'
        elif choice == '60':
            return '1m'
        elif choice == '120':
            return '2m'
        elif choice == '300':
            return '5m'
        elif choice == '600':
            return '10m'

        return 'Not Selected'

# Microphone shinanigens ------------------------------------------------------------------------
    # Changes the mic depending on what the user selected
    def selectmic(self, choice):
        self.current_mic = str(choice)
        self.main.write_to_file('mic', str(choice))
        self.main.audio.restart()  # Re-open capture on the newly selected device

    def mic_status_updater(self):
        if self.mic_enabled == 1:
            self.mic_enabled = 0
        else:
            self.mic_enabled = 1
        self.main.write_to_file('mic_enabled',self.mic_enabled)
        self.main.audio.restart()  # Enable/disable the mic source immediately

# Internal mic (yippee)
    # Changes the internal mic depending on what the user selected
    def select_internal_mic(self, choice):
        self.current_internal_mic = str(choice)
        self.main.write_to_file('internal_mic', str(choice))
        self.main.audio.restart()  # Re-open loopback capture on the newly selected device

    def internal_audio_status_updater(self):
        if self.internal_mic_enabled == 1:
            self.internal_mic_enabled = 0
        else:
            self.internal_mic_enabled = 1
        self.main.write_to_file('internal_mic_enabled',self.internal_mic_enabled)
        self.main.audio.restart()  # Enable/disable the computer-audio source immediately

# Mouse capture toggle
    def mouse_status_updater(self):
        if self.mouse_enabled == 1:
            self.mouse_enabled = 0
        else:
            self.mouse_enabled = 1
        self.main.write_to_file('mouse_enabled', self.mouse_enabled)
        # The capture thread reads self.mouse_enabled live, so it takes effect at once

    def getaudio(self,choice):
        input_mics = []

        # We get the exact system API name (e.g., "MME" or "Windows WASAPI")
        print("Type of choice:", type(choice), "Value:", choice)

        # Loop through all available host APIs
        api_name = None
        for i, api in enumerate(sd.query_hostapis()):
            if api['name'] == choice:
                api_name = api['name']
                # If you also need the integer ID later for device selection:
                # api_index = i
                break

        if api_name is None:
            # Fallback default just in case the string didn't match anything
            api_name = sd.query_hostapis(sd.default.hostapi)['name']

        # Combine them into a completely distinct, unmistakable name
        full_unique_name = f"{choice}, {api_name}"

        if full_unique_name not in input_mics:
            input_mics.append(full_unique_name)

        print(input_mics[0])
        return input_mics[0]

 # ------------------------------------------------------------------------------------------------------


