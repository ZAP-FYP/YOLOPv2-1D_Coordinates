import os
import subprocess
# Directory containing the video files
video_folder = "data/videos-long/chunks/IMG_0251.MOV"

# Get a list of all files in the directory
video_files = os.listdir(video_folder)

# Filter out only video files (assuming all video files have the .MOV extension)
# video_files = [file for file in video_files if file.endswith('.MOV')]

# Construct the commands list
commands = []
for file in video_files:
    source = os.path.join(video_folder, file)
    command = f"python demo.py --source {source} --device cpu"

    commands.append(command)

# Print the commands list
for command in commands:
    print(f"Executing {command} ")
    subprocess.run(command, shell=True)
