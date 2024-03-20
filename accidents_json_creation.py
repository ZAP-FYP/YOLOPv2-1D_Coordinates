import json
import os
import numpy as np

# Function to process a single JSON file
def process_json_file(json_file_path):
    # Load original JSON file
    with open(json_file_path) as f:
        original_data = json.load(f)

    # Extract anomaly start and end frame IDs
    anomaly_start_frame = original_data["anomaly_start"]
    anomaly_end_frame = original_data["anomaly_end"]

    # Define the frames to label as anomaly (30 frames before anomaly start to anomaly end)
    frames_to_label = list(range(anomaly_start_frame - 30, anomaly_end_frame + 1))

    # Initialize list to store new JSON data
    new_json_data = []

    # Iterate through frames
    for frame in original_data["labels"]:
        frame_id = frame["frame_id"]
        image_path = frame["image_path"]
        objects = frame["objects"]

        # Check if the frame is within anomaly range or 30 frames ahead
        if frame_id in frames_to_label:
            # Load numpy file and extract coordinates
            numpy_file_path = f'separate_numpy_for_eachimage/u33fdjUY_Iw_004735/images/{frame_id:06d}.npy'
            if os.path.exists(numpy_file_path):
                numpy_data = np.load(numpy_file_path)
                # Append coordinates to the new JSON data
                new_json_data.append({
                    "frame_id": frame_id,
                    "image_path": image_path,
                    "numpy_coordinates": numpy_data.tolist(),  # Convert numpy array to list
                    "prediction_label": 1  # Anomaly label
                })
            else:
                print(f"Warning: Numpy file not found for frame {frame_id}. Skipping.")
        else:
            # Append coordinates with normal label (0)
            numpy_file_path = f'separate_numpy_for_eachimage/u33fdjUY_Iw_004735/images/{frame_id:06d}.npy'
            if os.path.exists(numpy_file_path):
                numpy_data = np.load(numpy_file_path)
                # Append coordinates with normal label (0) and numpy coordinates
                new_json_data.append({
                    "frame_id": frame_id,
                    "image_path": image_path,
                    "numpy_coordinates": numpy_data.tolist(),  # Convert numpy array to list
                    "prediction_label": 0  # Normal label
                })
            else:
                print(f"Warning: Numpy file not found for frame {frame_id}. Skipping.")

    # Write new JSON data to a file
    output_dir = 'json'  # Specify your desired output directory
    output_filename = os.path.basename(json_file_path).replace('.json', '_with_numpy.json')
    output_path = os.path.join(output_dir, output_filename)

    with open(output_path, 'w') as f:
        json.dump(new_json_data, f, indent=4)

    print(f"New JSON file '{output_filename}' with numpy coordinates created successfully.")

# Iterate through each JSON file in the directory
input_dir = 'DoTA_annotations'
for filename in os.listdir(input_dir):
    if filename.endswith('.json'):
        json_file_path = os.path.join(input_dir, filename)
        process_json_file(json_file_path)
print("All JSON files processed successfully.")
