import csv

# 1. Open your actual file name
with open('defaults.csv', newline='', encoding='utf-8') as csvfile:
    # 2. Use standard comma separation (delimiter=',' is the default!)
    reader = csv.reader(csvfile)

    # 3. Loop through and do whatever you want with the data
    for row in reader:
        # 'row' is just a list. You can access columns by index:
        # row[0] is the first column, row[1] is the second, etc.
        print(row[1])