from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from moviepy.editor import VideoFileClip

# Define the input video file name
input_video = "data/videos-long/video-C1-20221124-1.mp4"

# Load the input video and get its duration in seconds
video = VideoFileClip(input_video)
total_duration = video.duration

# Duration of each subclip in seconds (1 minute in this case)
clip_duration = 60

# Calculate the number of 1-minute subclips needed
num_clips = int(total_duration / clip_duration)

# Create 1-minute subclips
for i in range(num_clips):
    start_time = i * clip_duration
    end_time = min((i + 1) * clip_duration, total_duration)
    output_file = f"data/videos-long/chunks/video-C1-20221124-1.mp4/{i + 1}.mp4"
    ffmpeg_extract_subclip(input_video, start_time, end_time, targetname=output_file)
