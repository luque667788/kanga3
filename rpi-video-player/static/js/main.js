document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const videoFile = document.getElementById('videoFile');
    const uploadStatus = document.getElementById('uploadStatus');
    const playlistElement = document.getElementById('playlist');
    const playBtn = document.getElementById('playBtn');
    const pauseBtn = document.getElementById('pauseBtn');
    const stopBtn = document.getElementById('stopBtn');
    const prevBtn = document.getElementById('prevBtn');
    const nextBtn = document.getElementById('nextBtn');
    const playbackStatusElement = document.getElementById('playbackStatus');
    const currentVideoElement = document.getElementById('currentVideo');

    let currentPlaylist = [];
    let currentlyPlayingIndex = -1;
    let sortableInstance = null;

    // Fetch initial playlist and status
    fetchPlaylist();
    fetchPlaybackStatus();
    setInterval(fetchPlaybackStatus, 3000); // Periodically update status

    // --- Playlist Management ---
    async function fetchPlaylist() {
        try {
            const response = await fetch('/api/videos');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            currentPlaylist = await response.json();
            renderPlaylist();
        } catch (error) {
            console.error('Error fetching playlist:', error);
            playlistElement.innerHTML = '<li>Error loading playlist.</li>';
        }
    }

    function renderPlaylist() {
        playlistElement.innerHTML = '';
        if (currentPlaylist.length === 0) {
            playlistElement.innerHTML = '<li>Playlist is empty. Upload some videos!</li>';
        }
        currentPlaylist.forEach((video, index) => {
            const listItem = document.createElement('li');
            listItem.textContent = video.name;
            listItem.dataset.filename = video.filename;
            listItem.dataset.index = index;

            // Highlight currently playing video
            if (index === currentlyPlayingIndex && playbackStatusElement.textContent.includes('Playing')) {
                listItem.classList.add('playing');
            }

            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = 'Delete';
            deleteBtn.classList.add('delete-btn');
            deleteBtn.onclick = (e) => {
                e.stopPropagation(); // Prevent triggering play on click
                deleteVideo(video.filename);
            };

            listItem.appendChild(deleteBtn);
            listItem.addEventListener('click', () => {
                playVideo(video.filename);
            });
            playlistElement.appendChild(listItem);
        });
        makeSortable();
    }

    function makeSortable() {
        // Basic drag-and-drop reordering
        // For a more robust solution, consider a library like SortableJS
        // This is a simplified implementation.
        let draggedItem = null;

        playlistElement.querySelectorAll('li').forEach(item => {
            item.draggable = true;

            item.addEventListener('dragstart', (e) => {
                draggedItem = e.target;
                setTimeout(() => e.target.classList.add('dragging'), 0);
            });

            item.addEventListener('dragend', (e) => {
                e.target.classList.remove('dragging');
                draggedItem = null;
                updatePlaylistOrder();
            });

            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                const afterElement = getDragAfterElement(playlistElement, e.clientY);
                if (afterElement == null) {
                    playlistElement.appendChild(draggedItem);
                } else {
                    playlistElement.insertBefore(draggedItem, afterElement);
                }
            });
        });
    }

    function getDragAfterElement(container, y) {
        const draggableElements = [...container.querySelectorAll('li:not(.dragging)')];
        return draggableElements.reduce((closest, child) => {
            const box = child.getBoundingClientRect();
            const offset = y - box.top - box.height / 2;
            if (offset < 0 && offset > closest.offset) {
                return { offset: offset, element: child };
            } else {
                return closest;
            }
        }, { offset: Number.NEGATIVE_INFINITY }).element;
    }

    async function updatePlaylistOrder() {
        const newOrderedFilenames = Array.from(playlistElement.querySelectorAll('li')).map(li => li.dataset.filename);
        if (newOrderedFilenames.length !== currentPlaylist.length) {
            console.warn("Playlist length mismatch after reorder. Re-fetching.");
            fetchPlaylist(); // Re-fetch to be safe
            return;
        }
        try {
            const response = await fetch('/api/playlist/reorder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ playlist: newOrderedFilenames })
            });
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const result = await response.json();
            console.log('Playlist reordered:', result.message);
            // Update local playlist order to match new reality, then re-render
            const newPlaylist = newOrderedFilenames.map(filename => 
                currentPlaylist.find(video => video.filename === filename)
            );
            currentPlaylist = newPlaylist.filter(Boolean); // Filter out any undefined if mismatches occurred
            fetchPlaybackStatus(); // Update current playing index based on new order
            renderPlaylist(); // Re-render with new order
        } catch (error) {
            console.error('Error reordering playlist:', error);
            // Optionally revert UI or notify user
            fetchPlaylist(); // Re-fetch to correct UI if reorder failed
        }
    }

    // --- Video Upload ---
    uploadForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        if (!videoFile.files || videoFile.files.length === 0) {
            uploadStatus.textContent = 'Please select a video file.';
            uploadStatus.className = 'status-error';
            return;
        }
        const formData = new FormData();
        formData.append('video', videoFile.files[0]);
        uploadStatus.textContent = 'Uploading...';
        uploadStatus.className = 'status-pending';

        try {
            const response = await fetch('/api/videos/upload', {
                method: 'POST',
                body: formData
            });
            const result = await response.json();
            if (response.ok) {
                uploadStatus.textContent = `Success: ${result.message} (${result.filename})`;
                uploadStatus.className = 'status-success';
                videoFile.value = ''; // Clear file input
                fetchPlaylist(); // Refresh playlist
            } else {
                uploadStatus.textContent = `Error: ${result.error || 'Upload failed'}`;
                uploadStatus.className = 'status-error';
            }
        } catch (error) {
            console.error('Error uploading video:', error);
            uploadStatus.textContent = 'Upload failed. See console for details.';
            uploadStatus.className = 'status-error';
        }
    });

    // --- Video Deletion ---
    async function deleteVideo(filename) {
        if (!confirm(`Are you sure you want to delete ${filename}?`)) return;
        try {
            const response = await fetch(`/api/videos/${filename}`, { method: 'DELETE' });
            const result = await response.json();
            if (response.ok) {
                console.log('Video deleted:', result.message);
                fetchPlaylist(); // Refresh playlist
                fetchPlaybackStatus(); // Refresh status as deleted video might have been playing
            } else {
                alert(`Error deleting video: ${result.error}`);
            }
        } catch (error) {
            console.error('Error deleting video:', error);
            alert('Failed to delete video. See console.');
        }
    }

    // --- Playback Controls ---
    async function sendPlaybackCommand(command, filename = null) {
        let url = `/api/playback/${command}`;
        let body = {};
        if (filename) {
            body.filename = filename;
        }

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: Object.keys(body).length ? JSON.stringify(body) : null
            });
            if (!response.ok) {
                const errorResult = await response.json().catch(() => ({error: "Unknown error"}));
                throw new Error(`HTTP error! status: ${response.status}, message: ${errorResult.error}`);
            }
            const result = await response.json();
            console.log(`Command ${command} successful:`, result.message);
            fetchPlaybackStatus(); // Update UI based on new state
            return result;
        } catch (error) {
            console.error(`Error sending ${command} command:`, error);
            playbackStatusElement.textContent = `Error: ${error.message}`;
            playbackStatusElement.className = 'status-error';
            return null;
        }
    }

    function playVideo(filename = null) {
        sendPlaybackCommand('play', filename);
    }

    playBtn.addEventListener('click', () => playVideo()); // Play current/next or first
    pauseBtn.addEventListener('click', () => sendPlaybackCommand('pause'));
    stopBtn.addEventListener('click', () => sendPlaybackCommand('stop'));
    nextBtn.addEventListener('click', () => sendPlaybackCommand('next'));
    prevBtn.addEventListener('click', () => sendPlaybackCommand('previous'));

    // --- Playback Status Update ---
    async function fetchPlaybackStatus() {
        try {
            const response = await fetch('/api/playback/status');
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const status = await response.json();

            if (status.isPlaying) {
                playbackStatusElement.textContent = `Status: Playing`;
                playbackStatusElement.className = 'status-playing';
                if (status.currentVideo) {
                    currentVideoElement.textContent = `Current Video: ${status.currentVideo.name}`;
                    currentlyPlayingIndex = status.currentIndex;
                } else {
                    currentVideoElement.textContent = 'Current Video: Unknown';
                    currentlyPlayingIndex = -1;
                }
            } else {
                playbackStatusElement.textContent = 'Status: Stopped/Idle';
                playbackStatusElement.className = 'status-idle';
                currentVideoElement.textContent = 'Current Video: None';
                // If omxplayer is truly stopped (not just paused and not black screen), reset index
                // The backend status might need to be more nuanced about "stopped" vs "paused"
                // For now, if backend says not isPlaying, we assume it's fully stopped or black screen.
                // If it was black screen, currentVideo.name would be 'Black Screen'
                if (!status.currentVideo || status.currentVideo.name !== 'Black Screen'){
                    // currentlyPlayingIndex = -1; // Let backend manage this primarily
                } else if (status.currentVideo && status.currentVideo.name === 'Black Screen') {
                     currentVideoElement.textContent = `Current Video: Black Screen`;
                }
            }
            renderPlaylist(); // Re-render to update highlighting of playing item
        } catch (error) {
            console.error('Error fetching playback status:', error);
            playbackStatusElement.textContent = 'Status: Error fetching status';
            playbackStatusElement.className = 'status-error';
            currentVideoElement.textContent = 'Current Video: Unknown';
            // currentlyPlayingIndex = -1;
            renderPlaylist();
        }
    }
});
