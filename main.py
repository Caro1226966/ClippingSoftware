import csv

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

    # Writes whatever the input is to wherever it needs to go
    def write_to_file(self,pointer, value):
        updated_file = []
        with open('defaults.csv', 'r+') as csvfile:
            reader = csv.reader(csvfile)

            for line in reader:
                if line[0] == pointer:
                    line[1] = value

                updated_file.append(line) # adds the line as a new component in the list

        # re-writes the file to put in the new values accurately
        with open('defaults.csv','w',newline='') as csvfile:
            csvwriter = csv.writer(csvfile)
            csvwriter.writerows(updated_file)

program = MainProgram()