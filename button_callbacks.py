from config import *

class Callbacks:
    def __init__(self, main):

        # Clipping key flags
        self.clipping_key = CLIP_KEY
        self.setbutton_pressed = False

        self.main = main # Main Program link

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


    def selectmonitor(self):
        pass

