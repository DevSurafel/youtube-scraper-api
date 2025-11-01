from flask import Flask, request, jsonify
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend requests

def extract_channel_info(channel_url):
    """
    Scrapes YouTube channel name and subscriber count
    """
    try:
        # Configure Chrome options for headless browsing
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        
        # Initialize the driver - Render has Chrome pre-installed
        driver = webdriver.Chrome(options=chrome_options)
        
        # Load the channel page
        driver.get(channel_url)
        
        # Wait for the page to load
        wait = WebDriverWait(driver, 10)
        
        # Extract channel name
        channel_name = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "yt-formatted-string.ytd-channel-name"))
        ).text
        
        # Extract subscriber count
        # YouTube uses different selectors, try multiple approaches
        try:
            subscriber_element = driver.find_element(By.ID, "subscriber-count")
            subscriber_count = subscriber_element.get_attribute("aria-label")
            # Extract just the number from aria-label (e.g., "1.2 million subscribers")
            if subscriber_count:
                subscriber_count = subscriber_count.split("subscriber")[0].strip()
        except:
            # Fallback method
            try:
                subscriber_element = driver.find_element(By.CSS_SELECTOR, "#subscriber-count")
                subscriber_count = subscriber_element.text
            except:
                subscriber_count = "Subscriber count not available"
        
        driver.quit()
        
        return {
            'success': True,
            'channel_name': channel_name,
            'subscriber_count': subscriber_count,
            'channel_url': channel_url
        }
        
    except Exception as e:
        if 'driver' in locals():
            driver.quit()
        return {
            'success': False,
            'error': str(e)
        }

@app.route('/api/scrape', methods=['POST'])
def scrape_channel():
    """
    API endpoint to scrape YouTube channel
    Expects JSON: {"url": "youtube_channel_url"}
    """
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
    
    result = extract_channel_info(channel_url)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
