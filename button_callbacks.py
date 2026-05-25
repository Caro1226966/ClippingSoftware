from config import *

class Callbacks:
    def __init__(self, main):
        self.main = main # Main Program link


        # Clipping key flags
        self.clipping_key = CLIP_KEY
        self.setbutton_pressed = False

        # Monitor selector flags
        self.monitor = int(main.read_from_file('monitor'))

        # FPS
        self.fps = int(main.read_from_file('fps'))

        # Clip Length
        self.clip_length = int(main.read_from_file('clip_length'))

        # Output
        self.file_name = self.create_file_name()


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


    def selectmonitor(self, choice):
        self.monitor = int(choice[-1])

        print(self.monitor)
        self.main.write_to_file('monitor',str(self.monitor))

    def create_file_name(self):
        return str(time.time()) + '.mp4'

