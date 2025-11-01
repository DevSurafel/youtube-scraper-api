from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import os
import json
import time

app = Flask(__name__)
CORS(app)

# API Keys from environment variables
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

# ==================== YOUTUBE FUNCTIONS ====================

def extract_channel_identifier(url):
    """Extract channel ID or username from YouTube URL"""
    patterns = [
        (r'youtube\.com/channel/(UC[\w-]+)', 'id'),
        (r'youtube\.com/@([\w-]+)', 'handle'),
        (r'youtube\.com/c/([\w-]+)', 'custom'),
        (r'youtube\.com/user/([\w-]+)', 'user')
    ]
    
    for pattern, id_type in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), id_type
    return None, None

def format_count(count_str, suffix="subscribers"):
    """Format count in readable format"""
    try:
        count = int(count_str)
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B {suffix}"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M {suffix}"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K {suffix}"
        else:
            return f"{count:,} {suffix}"
    except:
        return f"Count hidden"

def get_youtube_channel_by_id(channel_id):
    """Get YouTube channel information using channel ID"""
    try:
        url = 'https://www.googleapis.com/youtube/v3/channels'
        params = {
            'part': 'snippet,statistics',
            'id': channel_id,
            'key': YOUTUBE_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            return {
                'success': False,
                'error': f"API Error: {data['error'].get('message', 'Unknown error')}"
            }
        
        if 'items' in data and len(data['items']) > 0:
            channel = data['items'][0]
            return {
                'success': True,
                'channel_name': channel['snippet']['title'],
                'subscriber_count': format_count(
                    channel['statistics'].get('subscriberCount', '0'),
                    'subscribers'
                ),
                'subscriber_count_exact': channel['statistics'].get('subscriberCount', '0'),
                'channel_url': f"https://www.youtube.com/channel/{channel_id}",
                'thumbnail': channel['snippet']['thumbnails']['high']['url'],
                'platform': 'youtube'
            }
        
        return {'success': False, 'error': 'Channel not found'}
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

def search_youtube_channel(handle):
    """Search for YouTube channel using handle"""
    try:
        handle = handle.lstrip('@')
        search_url = 'https://www.googleapis.com/youtube/v3/search'
        params = {
            'part': 'snippet',
            'q': handle,
            'type': 'channel',
            'key': YOUTUBE_API_KEY,
            'maxResults': 5
        }
        
        response = requests.get(search_url, params=params, timeout=10)
        data = response.json()
        
        if 'items' in data and len(data['items']) > 0:
            channel_id = data['items'][0]['snippet']['channelId']
            return get_youtube_channel_by_id(channel_id)
        
        return {'success': False, 'error': 'Channel not found'}
        
    except Exception as e:
        return {'success': False, 'error': f'Error: {str(e)}'}

# ==================== TWITTER SCRAPING FUNCTIONS ====================

def extract_twitter_username(url):
    """Extract username from Twitter/X URL"""
    patterns = [
        r'twitter\.com/([a-zA-Z0-9_]+)',
        r'x\.com/([a-zA-Z0-9_]+)',
        r'^@?([a-zA-Z0-9_]+)$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            username = match.group(1)
            if username.lower() not in ['home', 'explore', 'notifications', 'messages', 'i', 'search', 'settings', 'compose']:
                return username
    return None

def parse_follower_count(count_text):
    """Parse follower count text to number"""
    try:
        count_text = str(count_text).strip().upper().replace(',', '').replace(' ', '')
        
        if 'K' in count_text:
            return int(float(count_text.replace('K', '')) * 1000)
        elif 'M' in count_text:
            return int(float(count_text.replace('M', '')) * 1000000)
        elif 'B' in count_text:
            return int(float(count_text.replace('B', '')) * 1000000000)
        else:
            return int(float(count_text))
    except:
        return 0

def scrape_twitter_profile(username):
    """Scrape Twitter profile using multiple methods"""
    try:
        username = username.lstrip('@')
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        }
        
        # Method 1: Try updated Nitter instances
        nitter_instances = [
            'https://nitter.poast.org',
            'https://nitter.privacydev.net',
            'https://nitter.net',
            'https://nitter.unixfox.eu',
            'https://nitter.moomoo.me',
            'https://nitter.fly.dev'
        ]
        
        for nitter_url in nitter_instances:
            try:
                url = f'{nitter_url}/{username}'
                response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
                
                if response.status_code == 200 and len(response.text) > 1000:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Try multiple selectors for name
                    name_elem = (soup.find('a', class_='profile-card-fullname') or 
                                soup.find('div', class_='profile-card-fullname') or
                                soup.find('h1'))
                    name = name_elem.text.strip() if name_elem else username
                    
                    # Extract stats with multiple methods
                    stats_nums = soup.find_all('span', class_='profile-stat-num')
                    
                    followers = '0'
                    following = '0'
                    tweets = '0'
                    
                    if len(stats_nums) >= 3:
                        tweets = stats_nums[0].text.strip()
                        following = stats_nums[1].text.strip()
                        followers = stats_nums[2].text.strip()
                    else:
                        # Try alternate structure
                        stat_headers = soup.find_all('span', class_='profile-stat-header')
                        for header in stat_headers:
                            label = header.text.lower()
                            num_elem = header.find_previous_sibling('span', class_='profile-stat-num')
                            if num_elem:
                                if 'follower' in label:
                                    followers = num_elem.text.strip()
                                elif 'following' in label:
                                    following = num_elem.text.strip()
                                elif 'tweet' in label or 'post' in label:
                                    tweets = num_elem.text.strip()
                    
                    # Extract bio
                    bio_elem = soup.find('p', class_='profile-bio')
                    bio = bio_elem.text.strip() if bio_elem else ''
                    
                    # Extract profile image
                    img_elem = soup.find('a', class_='profile-card-avatar')
                    profile_image = ''
                    if img_elem:
                        img_tag = img_elem.find('img')
                        if img_tag and img_tag.get('src'):
                            img_src = img_tag['src']
                            if img_src.startswith('http'):
                                profile_image = img_src
                            else:
                                profile_image = nitter_url + img_src
                    
                    if not profile_image:
                        profile_image = f'https://unavatar.io/twitter/{username}'
                    
                    # Convert to numbers
                    follower_count = parse_follower_count(followers)
                    following_count = parse_follower_count(following)
                    tweet_count = parse_follower_count(tweets)
                    
                    # Only return if we got valid data
                    if follower_count > 0 or following_count > 0:
                        return {
                            'success': True,
                            'username': username,
                            'name': name,
                            'follower_count': format_count(str(follower_count), 'followers'),
                            'follower_count_exact': str(follower_count),
                            'following_count': format_count(str(following_count), 'following'),
                            'tweet_count': format_count(str(tweet_count), 'tweets'),
                            'profile_url': f"https://twitter.com/{username}",
                            'profile_image': profile_image,
                            'bio': bio,
                            'platform': 'twitter',
                            'method': f'nitter ({nitter_url})'
                        }
                    
            except Exception as e:
                print(f"Nitter {nitter_url} failed: {str(e)}")
                continue
        
        # Method 2: Try syndication API
        try:
            syndication_url = f'https://cdn.syndication.twimg.com/widgets/followbutton/info.json?screen_names={username}'
            response = requests.get(syndication_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0 and 'followers_count' in data[0]:
                    user_data = data[0]
                    
                    return {
                        'success': True,
                        'username': username,
                        'name': user_data.get('name', username),
                        'follower_count': format_count(str(user_data.get('followers_count', 0)), 'followers'),
                        'follower_count_exact': str(user_data.get('followers_count', 0)),
                        'following_count': format_count(str(user_data.get('friends_count', 0)), 'following'),
                        'tweet_count': format_count(str(user_data.get('statuses_count', 0)), 'tweets'),
                        'profile_url': f"https://twitter.com/{username}",
                        'profile_image': f'https://unavatar.io/twitter/{username}',
                        'bio': user_data.get('description', ''),
                        'platform': 'twitter',
                        'method': 'syndication'
                    }
        except Exception as e:
            print(f"Syndication API failed: {str(e)}")
        
        # Method 3: Use third-party API aggregator
        try:
            api_url = f'https://api.socialcounts.org/twitter-user-info/{username}'
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and 'followers' in data:
                    return {
                        'success': True,
                        'username': username,
                        'name': data.get('name', username),
                        'follower_count': format_count(str(data.get('followers', 0)), 'followers'),
                        'follower_count_exact': str(data.get('followers', 0)),
                        'following_count': format_count(str(data.get('following', 0)), 'following'),
                        'tweet_count': format_count(str(data.get('tweets', 0)), 'tweets'),
                        'profile_url': f"https://twitter.com/{username}",
                        'profile_image': f'https://unavatar.io/twitter/{username}',
                        'bio': data.get('description', ''),
                        'platform': 'twitter',
                        'method': 'socialcounts'
                    }
        except Exception as e:
            print(f"SocialCounts API failed: {str(e)}")
        
        return {
            'success': False,
            'error': f'Could not fetch data for @{username}. All scraping methods failed. The account may be private, suspended, or temporarily unavailable. Please try again later.'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'Error scraping Twitter profile: {str(e)}'
        }

# ==================== API ENDPOINTS ====================

@app.route('/api/scrape', methods=['POST'])
def scrape_social():
    """Universal endpoint for both YouTube and Twitter"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a URL'
        }), 400
    
    url = data['url'].strip()
    
    # Detect platform
    if 'youtube.com' in url or 'youtu.be' in url:
        # YouTube handling
        if not YOUTUBE_API_KEY:
            return jsonify({
                'success': False,
                'error': 'YouTube API not configured'
            }), 500
        
        identifier, id_type = extract_channel_identifier(url)
        if not identifier:
            return jsonify({
                'success': False,
                'error': 'Invalid YouTube URL'
            }), 400
        
        if id_type == 'id':
            result = get_youtube_channel_by_id(identifier)
        else:
            result = search_youtube_channel(identifier)
            
    elif 'twitter.com' in url or 'x.com' in url or url.startswith('@'):
        # Twitter handling
        username = extract_twitter_username(url)
        if not username:
            return jsonify({
                'success': False,
                'error': 'Invalid Twitter username or URL'
            }), 400
        
        result = scrape_twitter_profile(username)
        
    else:
        return jsonify({
            'success': False,
            'error': 'Unsupported platform. Please provide YouTube or Twitter/X URL.'
        }), 400
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@app.route('/api/youtube', methods=['POST'])
def scrape_youtube():
    """YouTube-specific endpoint"""
    if not YOUTUBE_API_KEY:
        return jsonify({
            'success': False,
            'error': 'YouTube API not configured'
        }), 500
    
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a YouTube channel URL'
        }), 400
    
    url = data['url'].strip()
    identifier, id_type = extract_channel_identifier(url)
    
    if not identifier:
        return jsonify({
            'success': False,
            'error': 'Invalid YouTube URL'
        }), 400
    
    if id_type == 'id':
        result = get_youtube_channel_by_id(identifier)
    else:
        result = search_youtube_channel(identifier)
    
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/twitter', methods=['POST'])
def scrape_twitter():
    """Twitter-specific endpoint using web scraping"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({
            'success': False,
            'error': 'Please provide a Twitter username or URL'
        }), 400
    
    url = data['url'].strip()
    username = extract_twitter_username(url)
    
    if not username:
        return jsonify({
            'success': False,
            'error': 'Invalid Twitter username or URL'
        }), 400
    
    result = scrape_twitter_profile(username)
    return jsonify(result), 200 if result['success'] else 404

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'twitter_method': 'multi-source scraping',
        'message': 'Twitter uses multiple scraping sources for reliability'
    }), 200

@app.route('/', methods=['GET'])
def home():
    """Root endpoint with API information"""
    return jsonify({
        'message': 'Social Media Info API',
        'version': '3.1',
        'supported_platforms': ['YouTube', 'Twitter/X'],
        'endpoints': {
            '/api/health': 'GET - Health check',
            '/api/scrape': 'POST - Universal scraper (auto-detects platform)',
            '/api/youtube': 'POST - YouTube-specific endpoint',
            '/api/twitter': 'POST - Twitter-specific endpoint (multi-source scraping)'
        },
        'youtube_configured': bool(YOUTUBE_API_KEY),
        'twitter_method': 'Multi-source web scraping (6+ fallback methods)'
    }), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
