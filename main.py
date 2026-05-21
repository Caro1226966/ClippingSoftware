from config import *
from button_callbacks import *


class MainProgram:
    def __init__(self):

        # Button Callbacks
        self.button_callback = Callbacks(self)

        # The clipping key to choose an active button
        self.choose_clipping_key = customtkinter.CTkButton(app, text='Select Clip Button',command = self.button_callback.setbutton)
        self.choose_clipping_key.pack(side="right", anchor="ne", padx=10, pady=30)

        # Text to show what button is the active clipping button
        self.current_button_text = customtkinter.CTkTextbox(app,width=200, height=30)
        self.current_button_text.pack(side="right", anchor="ne", padx=20, pady=30) # Padding
        self.current_button_text.insert(index=0.0,text=('Current Key: '+str(self.button_callback.clipping_key))) # Put in the text
        self.current_button_text.configure(state="disabled")

        # The mainloop that repeats the code above and keeps the window alive
        app.mainloop()

    def mainloop(self):
        if self.button_callback.clipping_key is not None:
            pass

    def write_to_file(self,pointer, value):
        with open('defaults.csv', 'r') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    line[1] = value
            print(reader)


program = MainProgram()