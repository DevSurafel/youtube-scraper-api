from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import re
import os

app = Flask(__name__)
CORS(app)

# Get your API key from https://console.cloud.google.com/
# Enable YouTube Data API v3
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', 'YOUR_API_KEY_HERE')

def extract_channel_id(url):
    """Extract channel ID from various YouTube URL formats"""
    patterns = [
        r'youtube\.com/channel/([\w-]+)',
        r'youtube\.com/c/([\w-]+)',
        r'youtube\.com/@([\w-]+)',
        r'youtube\.com/user/([\w-]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username_or_id = match.group(1)
            
            # If it's already a channel ID (starts with UC)
            if username_or_id.startswith('UC'):
                return username_or_id, 'id'
            else:
                return username_or_id, 'username'
    
    return None, None

def get_channel_info_by_id(channel_id):
    """Get channel info using channel ID"""
    url = f'https://www.googleapis.com/youtube/v3/channels'
    params = {
        'part': 'snippet,statistics',
        'id': channel_id,
        'key': YOUTUBE_API_KEY
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if 'items' in data and len(data['items']) > 0:
        channel = data['items'][0]
        return {
            'success': True,
            'channel_name': channel['snippet']['title'],
            'subscriber_count': format_subscriber_count(channel['statistics'].get('subscriberCount', '0')),
            'channel_url': f"https://www.youtube.com/channel/{channel_id}",
            'subscriber_count_exact': channel['statistics'].get('subscriberCount', '0')
        }
    
    return {'success': False, 'error': 'Channel not found'}

def get_channel_info_by_username(username):
    """Get channel info using username/handle"""
    # First, try to search for the channel
    search_url = f'https://www.googleapis.com/youtube/v3/search'
    search_params = {
        'part': 'snippet',
        'q': username,
        'type': 'channel',
        'key': YOUTUBE_API_KEY,
        'maxResults': 1
    }
    
    response = requests.get(search_url, params=search_params)
    data = response.json()
    
    if 'items' in data and len(data['items']) > 0:
        channel_id = data['items'][0]['snippet']['channelId']
        return get_channel_info_by_id(channel_id)
    
    return {'success': False, 'error': 'Channel not found'}

def format_subscriber_count(count):
    """Format subscriber count in readable format"""
    count = int(count)
    
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M subscribers"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K subscribers"
    else:
        return f"{count} subscribers"

@app.route('/api/scrape', methods=['POST'])
def scrape_channel():
    """API endpoint to get YouTube channel info"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a YouTube channel URL'
        }), 400
    
    channel_url = data['url']
    
    # Validate YouTube URL
    youtube_pattern = r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/(channel/|c/|user/|@)?[\w-]+'
    if not re.match(youtube_pattern, channel_url):
        return jsonify({
            'success': False,
            'error': 'Invalid YouTube URL'
        }), 400
    
    # Extract channel identifier
    identifier, id_type = extract_channel_id(channel_url)
    
    if not identifier:
        return jsonify({
            'success': False,
            'error': 'Could not extract channel information from URL'
        }), 400
    
    # Get channel info
    if id_type == 'id':
        result = get_channel_info_by_id(identifier)
    else:
        result = get_channel_info_by_username(identifier)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
