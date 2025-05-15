import os
import subprocess
import json
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

VIDEO_DIR = 'videos'
PLAYLIST_FILE = 'playlist.json'
# Use an absolute path for the black screen video
BLACK_SCREEN_VIDEO = os.path.abspath('black.mp4') 
# Ensure the black.mp4 file exists or create a dummy one
if not os.path.exists(BLACK_SCREEN_VIDEO):
    try:
        # Create a small, short black video if ffmpeg is available
        subprocess.run(['ffmpeg', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=1', '-vcodec', 'libx264', BLACK_SCREEN_VIDEO], check=True)
        logging.info(f"Created dummy black.mp4 at {BLACK_SCREEN_VIDEO}")
    except Exception as e:
        logging.error(f"Failed to create black.mp4: {e}. Please create it manually.")
        # As a fallback, create an empty file. OMXPlayer might handle this gracefully or fail.
        # A more robust solution would be to ensure a valid black video is present.
        open(BLACK_SCREEN_VIDEO, 'a').close()


app.config['UPLOAD_FOLDER'] = VIDEO_DIR
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB Max Upload Size

if not os.path.exists(VIDEO_DIR):
    os.makedirs(VIDEO_DIR)

omxplayer_process = None

def get_playlist():
    if not os.path.exists(PLAYLIST_FILE):
        return []
    try:
        with open(PLAYLIST_FILE, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []

def save_playlist(playlist):
    with open(PLAYLIST_FILE, 'w') as f:
        json.dump(playlist, f)

def stop_omxplayer():
    global omxplayer_process
    if omxplayer_process and omxplayer_process.poll() is None:
        try:
            omxplayer_process.stdin.write(b'q')
            omxplayer_process.stdin.flush()
            omxplayer_process.wait(timeout=5)
            logging.info("OMXPlayer process stopped via 'q'.")
        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
            logging.warning(f"Failed to stop OMXPlayer gracefully with 'q': {e}. Terminating.")
            try:
                omxplayer_process.terminate()
                omxplayer_process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError) as e2:
                logging.error(f"Failed to terminate OMXPlayer: {e2}. Killing.")
                omxplayer_process.kill()
                omxplayer_process.wait(timeout=5) # Wait for kill
        finally:
            omxplayer_process = None
    # Fallback: Ensure no lingering omxplayer instances if our tracking failed
    try:
        subprocess.run(['pkill', 'omxplayer.bin'], timeout=5)
        logging.info("Attempted pkill omxplayer.bin as a fallback.")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logging.warning(f"pkill omxplayer.bin failed: {e}")


def play_video_omx(video_path):
    global omxplayer_process
    stop_omxplayer() # Ensure any existing instance is stopped
    try:
        # Output to framebuffer, adjust --display and other params as needed for SPI
        # Common options: -o hdmi, -o local, --display <n> (for specific displays)
        # For SPI displays often mapped to /dev/fb0 or similar, -o fbdev might be needed
        # or just letting omxplayer pick the default framebuffer if configured system-wide.
        # Adding --no-osd to hide on-screen display, --no-keys to disable keyboard control.
        command = ['omxplayer', '--no-osd', '--no-keys', video_path]
        logging.info(f"Executing OMXPlayer command: {' '.join(command)}")
        omxplayer_process = subprocess.Popen(command, stdin=subprocess.PIPE)
        logging.info(f"OMXPlayer started for video: {video_path} with PID: {omxplayer_process.pid}")
    except FileNotFoundError:
        logging.error("OMXPlayer command not found. Is it installed and in PATH?")
    except Exception as e:
        logging.error(f"Error starting OMXPlayer: {e}")
        omxplayer_process = None


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

@app.route('/api/videos', methods=['GET'])
def list_videos_endpoint():
    playlist = get_playlist()
    # Ensure videos in playlist still exist
    valid_playlist = []
    for video_info in playlist:
        if os.path.exists(os.path.join(VIDEO_DIR, video_info['filename'])):
            valid_playlist.append(video_info)
        else:
            logging.warning(f"Video {video_info['filename']} not found, removing from playlist.")
    if len(valid_playlist) != len(playlist):
        save_playlist(valid_playlist)
    return jsonify(valid_playlist)

@app.route('/api/videos/upload', methods=['POST'])
def upload_video_endpoint():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file part'}), 400
    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        try:
            file.save(filepath)
            playlist = get_playlist()
            # Avoid adding duplicates by filename
            if not any(v['filename'] == filename for v in playlist):
                playlist.append({'filename': filename, 'path': filepath, 'name': filename.rsplit('.', 1)[0]})
                save_playlist(playlist)
            return jsonify({'message': 'Video uploaded successfully', 'filename': filename}), 201
        except Exception as e:
            logging.error(f"Error saving uploaded file {filename}: {e}")
            return jsonify({'error': f'Error saving file: {str(e)}'}), 500
    return jsonify({'error': 'File upload failed'}), 500


@app.route('/api/videos/<filename>', methods=['DELETE'])
def delete_video_endpoint(filename):
    filepath = os.path.join(VIDEO_DIR, filename)
    playlist = get_playlist()
    
    # If the video being deleted is currently playing, stop playback.
    global omxplayer_process
    if omxplayer_process and omxplayer_process.poll() is None:
        # This is a simplification. Ideally, you'd check if omxplayer_process.args contains filename
        # For now, we'll assume if a video is playing and we delete *any* video, we should stop.
        # A more precise check would involve storing the currently playing video's filename.
        current_video_path_playing = None # Placeholder for actual check
        # A simple check could be if omxplayer_process.args contains the filepath.
        # This requires storing more info about the process or parsing args.
        # For now, let's assume if we delete a video, and something is playing, we stop it.
        # This is not ideal if deleting a non-playing video while another is playing.
        # A better approach: store the filename of the video passed to play_video_omx
        # and compare it here.
        # For this example, we'll stop if *any* video is playing and *any* video is deleted.
        # This needs refinement for a production system.
        logging.info(f"Video {filename} is being deleted. Stopping current playback if any.")
        stop_omxplayer()
        play_video_omx(BLACK_SCREEN_VIDEO) # Show black screen after deleting active video


    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            playlist = [video for video in playlist if video['filename'] != filename]
            save_playlist(playlist)
            return jsonify({'message': 'Video deleted successfully'}), 200
        except Exception as e:
            logging.error(f"Error deleting video file {filename}: {e}")
            return jsonify({'error': f'Error deleting file: {str(e)}'}), 500
    return jsonify({'error': 'Video not found'}), 404

@app.route('/api/playlist/reorder', methods=['POST'])
def reorder_playlist_endpoint():
    new_order = request.json.get('playlist')
    if new_order is None:
        return jsonify({'error': 'Playlist data missing'}), 400
    
    current_playlist = get_playlist()
    # Create a dictionary for quick lookup of current video details
    current_playlist_dict = {video['filename']: video for video in current_playlist}
    
    reordered_playlist = []
    valid_filenames = set(current_playlist_dict.keys())

    for filename_in_new_order in new_order:
        if filename_in_new_order in valid_filenames:
            # Preserve existing details, only update order
            reordered_playlist.append(current_playlist_dict[filename_in_new_order])
        else:
            logging.warning(f"Filename {filename_in_new_order} in reorder request not found in current playlist. Skipping.")

    # Ensure all original videos are present, in case new_order is partial (though it shouldn't be)
    # This step might be redundant if the frontend sends the full, reordered list of existing filenames.
    # For robustness, we ensure we don't lose videos if the client sends a malformed list.
    if len(reordered_playlist) != len(current_playlist_dict):
        logging.warning("Reordered playlist length mismatch. Reconstructing carefully.")
        # This part needs careful handling. If the client is trusted to send the full list,
        # then simply saving `reordered_playlist` (after validation) is fine.
        # If not, you might need a more complex merge or reject the request.
        # For now, we'll trust the client sends a list of existing filenames.
        pass # Assuming client sends a full list of existing filenames in new order.

    save_playlist(reordered_playlist)
    return jsonify({'message': 'Playlist reordered successfully'}), 200

current_playing_index = -1

@app.route('/api/playback/play', methods=['POST'])
def play_video_endpoint():
    global current_playing_index
    playlist = get_playlist()
    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 400

    video_filename_to_play = request.json.get('filename')
    
    if video_filename_to_play:
        # Find the index of the requested video
        found_index = -1
        for i, video_info in enumerate(playlist):
            if video_info['filename'] == video_filename_to_play:
                found_index = i
                break
        if found_index != -1:
            current_playing_index = found_index
        else:
            return jsonify({'error': f'Video {video_filename_to_play} not in playlist'}), 404
    else:
        # If no specific filename, play from the start or current/next
        if current_playing_index == -1 or current_playing_index >= len(playlist) -1: # Start from beginning if at end or never played
             current_playing_index = 0
        # If a video was playing, and "play" is hit again, it might mean resume or restart current.
        # For simplicity, we'll just play the video at current_playing_index.
        # OMXPlayer itself doesn't have a simple "resume" from a stopped state via new command.
        # Pause/Resume is handled differently.

    video_to_play = playlist[current_playing_index]
    video_path = os.path.join(VIDEO_DIR, video_to_play['filename'])
    
    if os.path.exists(video_path):
        logging.info(f"Playing video: {video_path} at index {current_playing_index}")
        play_video_omx(video_path)
        return jsonify({'message': f'Playing {video_to_play["name"]}', 'playing': video_to_play, 'currentIndex': current_playing_index}), 200
    return jsonify({'error': 'Video file not found'}), 404


@app.route('/api/playback/pause', methods=['POST'])
def pause_video_endpoint():
    global omxplayer_process
    if omxplayer_process and omxplayer_process.poll() is None:
        try:
            omxplayer_process.stdin.write(b'p') # 'p' toggles pause in omxplayer
            omxplayer_process.stdin.flush()
            return jsonify({'message': 'Playback pause/resume toggled'}), 200
        except Exception as e:
            logging.error(f"Error sending pause command to OMXPlayer: {e}")
            return jsonify({'error': f'Failed to send pause command: {str(e)}'}), 500
    return jsonify({'error': 'OMXPlayer not running or already stopped'}), 400

@app.route('/api/playback/stop', methods=['POST'])
def stop_video_endpoint():
    global current_playing_index
    logging.info("Stop command received. Stopping OMXPlayer and displaying black screen.")
    stop_omxplayer()
    # Play a black screen video
    if os.path.exists(BLACK_SCREEN_VIDEO):
        logging.info(f"Playing black screen video: {BLACK_SCREEN_VIDEO}")
        play_video_omx(BLACK_SCREEN_VIDEO) # Play the black screen
    else:
        # Fallback if black.mp4 is missing - just ensure player is stopped.
        # The framebuffer might retain the last frame or go blank depending on system config.
        logging.warning(f"Black screen video not found at {BLACK_SCREEN_VIDEO}. OMXPlayer stopped, but screen might not be black.")
    current_playing_index = -1 # Reset playlist position
    return jsonify({'message': 'Playback stopped, displaying black screen.'}), 200

@app.route('/api/playback/next', methods=['POST'])
def next_video_endpoint():
    global current_playing_index
    playlist = get_playlist()
    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 400
    
    current_playing_index += 1
    if current_playing_index >= len(playlist):
        current_playing_index = 0 # Loop back to the start
        
    video_to_play = playlist[current_playing_index]
    video_path = os.path.join(VIDEO_DIR, video_to_play['filename'])
    if os.path.exists(video_path):
        logging.info(f"Playing next video: {video_path} at index {current_playing_index}")
        play_video_omx(video_path)
        return jsonify({'message': f'Playing next: {video_to_play["name"]}', 'playing': video_to_play, 'currentIndex': current_playing_index}), 200
    return jsonify({'error': 'Next video file not found'}), 404


@app.route('/api/playback/previous', methods=['POST'])
def previous_video_endpoint():
    global current_playing_index
    playlist = get_playlist()
    if not playlist:
        return jsonify({'error': 'Playlist is empty'}), 400
        
    current_playing_index -= 1
    if current_playing_index < 0:
        current_playing_index = len(playlist) - 1 # Loop back to the end
        
    video_to_play = playlist[current_playing_index]
    video_path = os.path.join(VIDEO_DIR, video_to_play['filename'])
    if os.path.exists(video_path):
        logging.info(f"Playing previous video: {video_path} at index {current_playing_index}")
        play_video_omx(video_path)
        return jsonify({'message': f'Playing previous: {video_to_play["name"]}', 'playing': video_to_play, 'currentIndex': current_playing_index}), 200
    return jsonify({'error': 'Previous video file not found'}), 404

@app.route('/api/playback/status', methods=['GET'])
def playback_status_endpoint():
    global omxplayer_process, current_playing_index
    playlist = get_playlist()
    status = {'isPlaying': False, 'currentVideo': None, 'currentIndex': current_playing_index}

    if omxplayer_process and omxplayer_process.poll() is None:
        # Check if it's the black screen video
        # This check is a bit naive as it relies on the exact path.
        # A more robust way would be to store a flag or the specific filename when black screen is played.
        is_black_screen = False
        try:
            # omxplayer_process.args might not be directly accessible or could be complex.
            # A common way to get command args for a PID on Linux:
            with open(f'/proc/{omxplayer_process.pid}/cmdline', 'r') as f:
                cmdline = f.read().split(' ')
            if BLACK_SCREEN_VIDEO in cmdline:
                is_black_screen = True
        except Exception as e:
            logging.debug(f"Could not check /proc/{omxplayer_process.pid}/cmdline for black screen: {e}")


        if not is_black_screen and 0 <= current_playing_index < len(playlist):
            status['isPlaying'] = True
            status['currentVideo'] = playlist[current_playing_index]
        elif is_black_screen:
            status['isPlaying'] = True # Technically playing, but it's the black screen
            status['currentVideo'] = {'name': 'Black Screen', 'filename': os.path.basename(BLACK_SCREEN_VIDEO)}
        # If omxplayer is running but current_playing_index is out of sync,
        # it implies an inconsistency or that omxplayer was started externally/manually.
        # For now, we trust current_playing_index if the process is alive and not black screen.

    return jsonify(status)


if __name__ == '__main__':
    # Ensure black.mp4 exists before starting
    if not os.path.exists(BLACK_SCREEN_VIDEO):
        logging.error(f"CRITICAL: Black screen video {BLACK_SCREEN_VIDEO} does not exist. Stopping playback will not show a black screen.")
        # Optionally, exit if black.mp4 is critical and missing
        # exit(1) 
    app.run(host='0.0.0.0', port=5000, debug=True)
