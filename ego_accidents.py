import json
import os 
import shutil

with open('DoTA_videos/metadata_train.json') as f:
    data = json.load(f)

# Extract anomaly_class values
anomaly_classes = set(entry['anomaly_class'] for entry in data.values())

ego_accident_types = [item for item in anomaly_classes if 'other' not in item]
ego_accidents = {key: value for key, value in data.items() if value['anomaly_class'] in ego_accident_types}

# Save ego_accidents dictionary as a JSON file
# with open("DoTA_videos/ego_accidents", 'w') as json_file:
#     json.dump(ego_accidents, json_file, indent=4)

source_folder = 'DoTA_videos/'
destination_folder = 'DoTA_ego_videos/'

# Iterate through each video ID in the filtered_data
for video_id in ego_accidents:
    # Construct the file paths for the source and destination videos
    source_video_path = os.path.join(source_folder, video_id)
    destination_video_path = os.path.join(destination_folder, video_id)
    
    # Move the video file to the destination folder
    if os.path.exists(source_video_path):
        shutil.move(source_video_path, destination_video_path)
        print(f"Moved video {video_id} to {destination_folder}")