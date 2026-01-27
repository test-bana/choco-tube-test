import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib.parse
import datetime
import random
import time
import tempfile
import subprocess
import re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session, send_file, after_this_request
from functools import wraps
import yt_dlp

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.secret_key = os.environ.get('SESSION_SECRET', os.environ.get('SECRET_KEY', 'choco-tube-secret-key-2025'))

# セッションクッキーの設定（Render等のHTTPS環境で必要）
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('RENDER', False) or os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

PASSWORD = os.environ.get('APP_PASSWORD', 'choco')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

# YouTube API Keys for rotation
YOUTUBE_API_KEYS = [
    "AIzaSyCz7f0X_giaGyC9u1EfGZPBuAC9nXiL5Mo",
    "AIzaSyBmzCw7-sX1vm-uL_u2Qy3LuVZuxye4Wys",
    "AIzaSyBWScla0K91jUL6qQErctN9N2b3j9ds7HI",
    "AIzaSyA17CdOQtQRC3DQe7rgIzFwTUjwAy_3CAc",
    "AIzaSyDdk_yY0tN4gKsm4uyMYrIlv1RwXIYXrnw",
    "AIzaSyDeU5zpcth2OgXDfToyc7-QnSJsDc41UGk",
    "AIzaSyClu2V_22XpCG2GTe1euD35_Mh5bn4eTjA"
]
_current_api_key_index = 0

EDU_VIDEO_API = "https://siawaseok.duckdns.org/api/video2/"
EDU_CONFIG_URL = "https://raw.githubusercontent.com/siawaseok3/wakame/master/video_config.json"
STREAM_API = "https://ytdl-0et1.onrender.com/stream/"
M3U8_API = "https://ytdl-0et1.onrender.com/m3u8/"

EDU_PARAM_SOURCES = {
    'siawaseok': {
        'name': '幸せok',
        'url': 'https://raw.githubusercontent.com/siawaseok3/wakame/master/video_config.json',
        'type': 'json_params'
    },
    'woolisbest1': {
        'name': 'woolisbest1',
        'url': 'https://raw.githubusercontent.com/woolisbest-4520/about-youtube/refs/heads/main/edu.json',
        'type': 'json_params'
    },
    'woolisbest2': {
        'name': 'woolisbest2',
        'url': 'https://raw.githubusercontent.com/woolisbest-4520/about-youtube/refs/heads/main/parameter.json',
        'type': 'json_params'
    },
    'kahoot': {
        'name': 'その他',
        'url': 'https://apis.kahoot.it/media-api/youtube/key',
        'type': 'kahoot_key'
    }
}

_edu_params_cache = {}
_edu_cache_timestamp = {}
_trending_cache = {'data': None, 'timestamp': 0}
_thumbnail_cache = {}

http_session = requests.Session()
retry_strategy = Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:92.0) Gecko/20100101 Firefox/92.0',
]

INVIDIOUS_INSTANCES = [
    'https://inv.nadeko.net/',
    'https://invidious.f5.si/',
    'https://invidious.lunivers.trade/',
    'https://invidious.ducks.party/',
    'https://super8.absturztau.be/',
    'https://invidious.nikkosphere.com/',
    'https://yt.omada.cafe/',
    'https://iv.melmac.space/',
    'https://iv.duti.dev/',
]

def get_random_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS)
    }

def get_edu_params(source='siawaseok'):
    cache_duration = 300
    current_time = time.time()

    if source in _edu_params_cache and source in _edu_cache_timestamp:
        if (current_time - _edu_cache_timestamp[source]) < cache_duration:
            return _edu_params_cache[source]

    source_config = EDU_PARAM_SOURCES.get(source, EDU_PARAM_SOURCES['siawaseok'])

    try:
        res = http_session.get(source_config['url'], headers=get_random_headers(), timeout=3)
        res.raise_for_status()

        if source_config['type'] == 'kahoot_key':
            data = res.json()
            api_key = data.get('key', '')
            if api_key:
                params = f"autoplay=1&rel=0&modestbranding=1&key={api_key}"
            else:
                params = "autoplay=1&rel=0&modestbranding=1"
        else:
            data = res.json()
            params = data.get('params', '')
            if params.startswith('?'):
                params = params[1:]
            params = params.replace('&amp;', '&')

        _edu_params_cache[source] = params
        _edu_cache_timestamp[source] = current_time
        return params
    except Exception as e:
        print(f"Failed to fetch edu params from {source}: {e}")
        return "autoplay=1&rel=0&modestbranding=1"

def safe_request(url, timeout=(2, 5)):
    try:
        res = http_session.get(url, headers=get_random_headers(), timeout=timeout)
        res.raise_for_status()
        return res.json()
    except:
        return None

def request_invidious_api(path, timeout=(2, 5)):
    random_instances = random.sample(INVIDIOUS_INSTANCES, min(3, len(INVIDIOUS_INSTANCES)))
    for instance in random_instances:
        try:
            url = instance + 'api/v1' + path
            res = http_session.get(url, headers=get_random_headers(), timeout=timeout)
            if res.status_code == 200:
                return res.json()
        except:
            continue
    return None

def get_youtube_search(query, max_results=20, use_api_keys=True):
    global _current_api_key_index

    if use_api_keys and YOUTUBE_API_KEYS:
        for attempt in range(len(YOUTUBE_API_KEYS)):
            key_index = (_current_api_key_index + attempt) % len(YOUTUBE_API_KEYS)
            api_key = YOUTUBE_API_KEYS[key_index]
            url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&q={urllib.parse.quote(query)}&maxResults={max_results}&key={api_key}"
            try:
                res = http_session.get(url, timeout=5)
                if res.status_code == 403:
                    print(f"YouTube API key {key_index + 1} quota exceeded, trying next...")
                    continue
                res.raise_for_status()
                data = res.json()
                results = []
                for item in data.get('items', []):
                    snippet = item.get('snippet', {})
                    results.append({
                        'type': 'video',
                        'id': item.get('id', {}).get('videoId', ''),
                        'title': snippet.get('title', ''),
                        'author': snippet.get('channelTitle', ''),
                        'authorId': snippet.get('channelId', ''),
                        'thumbnail': f"https://i.ytimg.com/vi/{item.get('id', {}).get('videoId', '')}/hqdefault.jpg",
                        'published': snippet.get('publishedAt', ''),
                        'description': snippet.get('description', ''),
                        'views': '',
                        'length': ''
                    })
                _current_api_key_index = (key_index + 1) % len(YOUTUBE_API_KEYS)
                return results
            except Exception as e:
                print(f"YouTube API key {key_index + 1} error: {e}")
                continue

        print("All YouTube API keys failed, falling back to Invidious")

    return invidious_search(query)

def get_invidious_search_first(query, max_results=20):
    global _current_api_key_index

    results = invidious_search(query)
    if results:
        return results

    print("Invidious search failed, falling back to YouTube API")

    if YOUTUBE_API_KEYS:
        for attempt in range(len(YOUTUBE_API_KEYS)):
            key_index = (_current_api_key_index + attempt) % len(YOUTUBE_API_KEYS)
            api_key = YOUTUBE_API_KEYS[key_index]
            url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&q={urllib.parse.quote(query)}&maxResults={max_results}&key={api_key}"
            try:
                res = http_session.get(url, timeout=5)
                if res.status_code == 403:
                    print(f"YouTube API key {key_index + 1} quota exceeded, trying next...")
                    continue
                res.raise_for_status()
                data = res.json()
                results = []
                for item in data.get('items', []):
                    snippet = item.get('snippet', {})
                    results.append({
                        'type': 'video',
                        'id': item.get('id', {}).get('videoId', ''),
                        'title': snippet.get('title', ''),
                        'author': snippet.get('channelTitle', ''),
                        'authorId': snippet.get('channelId', ''),
                        'thumbnail': f"https://i.ytimg.com/vi/{item.get('id', {}).get('videoId', '')}/hqdefault.jpg",
                        'published': snippet.get('publishedAt', ''),
                        'description': snippet.get('description', ''),
                        'views': '',
                        'length': ''
                    })
                _current_api_key_index = (key_index + 1) % len(YOUTUBE_API_KEYS)
                return results
            except Exception as e:
                print(f"YouTube API key {key_index + 1} error: {e}")
                continue

    return []

def invidious_search(query, page=1):
    path = f"/search?q={urllib.parse.quote(query)}&page={page}&hl=jp"
    data = request_invidious_api(path)

    if not data:
        return []

    results = []
    for item in data:
        item_type = item.get('type', '')

        if item_type == 'video':
            length_seconds = item.get('lengthSeconds', 0)
            results.append({
                'type': 'video',
                'id': item.get('videoId', ''),
                'title': item.get('title', ''),
                'author': item.get('author', ''),
                'authorId': item.get('authorId', ''),
                'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
                'published': item.get('publishedText', ''),
                'views': item.get('viewCountText', ''),
                'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
            })
        elif item_type == 'channel':
            thumbnails = item.get('authorThumbnails', [])
            thumb_url = thumbnails[-1].get('url', '') if thumbnails else ''
            if thumb_url and not thumb_url.startswith('https'):
                thumb_url = 'https:' + thumb_url
            results.append({
                'type': 'channel',
                'id': item.get('authorId', ''),
                'author': item.get('author', ''),
                'thumbnail': thumb_url,
                'subscribers': item.get('subCount', 0)
            })
        elif item_type == 'playlist':
            results.append({
                'type': 'playlist',
                'id': item.get('playlistId', ''),
                'title': item.get('title', ''),
                'thumbnail': item.get('playlistThumbnail', ''),
                'count': item.get('videoCount', 0)
            })

    return results

def get_video_info(video_id):
    path = f"/videos/{urllib.parse.quote(video_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        try:
            res = http_session.get(f"{EDU_VIDEO_API}{video_id}", headers=get_random_headers(), timeout=(2, 6))
            res.raise_for_status()
            edu_data = res.json()

            related_videos = []
            for item in edu_data.get('related', [])[:20]:
                related_videos.append({
                    'id': item.get('videoId', ''),
                    'title': item.get('title', ''),
                    'author': item.get('channel', ''),
                    'authorId': item.get('channelId', ''),
                    'views': item.get('views', ''),
                    'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/mqdefault.jpg",
                    'length': ''
                })

            return {
                'title': edu_data.get('title', ''),
                'description': edu_data.get('description', {}).get('formatted', ''),
                'author': edu_data.get('author', {}).get('name', ''),
                'authorId': edu_data.get('author', {}).get('id', ''),
                'authorThumbnail': edu_data.get('author', {}).get('thumbnail', ''),
                'views': edu_data.get('views', ''),
                'likes': edu_data.get('likes', ''),
                'subscribers': edu_data.get('author', {}).get('subscribers', ''),
                'published': edu_data.get('relativeDate', ''),
                'related': related_videos,
                'streamUrls': [],
                'highstreamUrl': None,
                'audioUrl': None
            }
        except Exception as e:
            print(f"EDU Video API error: {e}")
            return None

    recommended = data.get('recommendedVideos', data.get('recommendedvideo', []))
    related_videos = []
    for item in recommended[:20]:
        length_seconds = item.get('lengthSeconds', 0)
        related_videos.append({
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'views': item.get('viewCountText', ''),
            'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/mqdefault.jpg",
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    adaptive_formats = data.get('adaptiveFormats', [])
    stream_urls = []
    highstream_url = None
    audio_url = None

    # 音声付きストリームを探す (360p or 144p) - get_stream_urlと整合性を取る
    format_streams = data.get('formatStreams', [])
    primary_url = None
    
    # 360p 音声付きを探す
    for stream in format_streams:
        if stream.get('qualityLabel') == '360p' or stream.get('resolution') == '360p':
            primary_url = stream.get('url')
            break
            
    # 見つからない場合は 144p 音声付きを探す
    if not primary_url:
        for stream in format_streams:
            if stream.get('qualityLabel') == '144p' or stream.get('resolution') == '144p':
                primary_url = stream.get('url')
                break

    for stream in adaptive_formats:
        if stream.get('container') == 'webm' and stream.get('resolution'):
            stream_urls.append({
                'url': stream.get('url', ''),
                'resolution': stream.get('resolution', '')
            })
            if stream.get('resolution') == '1080p' and not highstream_url:
                highstream_url = stream.get('url')
            elif stream.get('resolution') == '720p' and not highstream_url:
                highstream_url = stream.get('url')

    for stream in adaptive_formats:
        if stream.get('container') == 'm4a' and stream.get('audioQuality') == 'AUDIO_QUALITY_MEDIUM':
            audio_url = stream.get('url')
            break

    format_streams = data.get('formatStreams', [])
    video_urls = [stream.get('url', '') for stream in reversed(format_streams)][:2]

    author_thumbnails = data.get('authorThumbnails', [])
    author_thumbnail = author_thumbnails[-1].get('url', '') if author_thumbnails else ''

    return {
        'title': data.get('title', ''),
        'description': data.get('descriptionHtml', '').replace('\n', '<br>'),
        'author': data.get('author', ''),
        'authorId': data.get('authorId', ''),
        'authorThumbnail': author_thumbnail,
        'thumbnail': f"/api/proxy-thumbnail?video_id={video_id}",
        'views': data.get('viewCount', 0),
        'likes': data.get('likeCount', 0),
        'subscribers': data.get('subCountText', ''),
        'published': data.get('publishedText', ''),
        'lengthText': str(datetime.timedelta(seconds=data.get('lengthSeconds', 0))),
        'related': related_videos,
        'videoUrls': video_urls,
        'streamUrls': stream_urls,
        'primaryUrl': primary_url,
        'highstreamUrl': highstream_url,
        'audioUrl': audio_url
    }

def get_playlist_info(playlist_id):
    path = f"/playlists/{urllib.parse.quote(playlist_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    videos = []
    for item in data.get('videos', []):
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    return {
        'id': playlist_id,
        'title': data.get('title', ''),
        'author': data.get('author', ''),
        'authorId': data.get('authorId', ''),
        'description': data.get('description', ''),
        'videoCount': data.get('videoCount', 0),
        'viewCount': data.get('viewCount', 0),
        'videos': videos
    }

def get_channel_info(channel_id):
    # Invidious API for channel info
    path = f"/channels/{urllib.parse.quote(channel_id)}"
    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    # Try different possible keys for videos in Invidious API
    latest_videos = data.get('latestVideos', data.get('latestvideo', data.get('videos', [])))
    videos = []
    for item in latest_videos:
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': data.get('author', ''),
            'authorId': data.get('authorId', ''),
            'published': item.get('publishedText', ''),
            'views': item.get('viewCountText', ''),
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    author_thumbnails = data.get('authorThumbnails', [])
    author_thumbnail = author_thumbnails[-1].get('url', '') if author_thumbnails else ''
    if author_thumbnail and not author_thumbnail.startswith('http'):
        author_thumbnail = 'https:' + author_thumbnail

    author_banners = data.get('authorBanners', [])
    author_banner = author_banners[0].get('url', '') if author_banners else ''
    if author_banner and not author_banner.startswith('http'):
        author_banner = 'https:' + author_banner

    return {
        'videos': videos,
        'channelName': data.get('author', ''),
        'channelIcon': author_thumbnail,
        'channelProfile': data.get('descriptionHtml', ''),
        'authorBanner': author_banner,
        'subscribers': data.get('subCount', 0),
        'tags': data.get('tags', []),
        'videoCount': data.get('videoCount', 0)
    }

def get_channel_videos(channel_id, continuation=None):
    path = f"/channels/{urllib.parse.quote(channel_id)}/videos"
    if continuation:
        path += f"?continuation={urllib.parse.quote(continuation)}"

    data = request_invidious_api(path, timeout=(5, 15))

    if not data:
        return None

    videos = []
    for item in data.get('videos', []):
        length_seconds = item.get('lengthSeconds', 0)
        videos.append({
            'type': 'video',
            'id': item.get('videoId', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'authorId': item.get('authorId', ''),
            'published': item.get('publishedText', ''),
            'views': item.get('viewCountText', ''),
            'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
        })

    return {
        'videos': videos,
        'continuation': data.get('continuation', '')
    }

def get_stream_url(video_id, edu_source='siawaseok'):
    edu_params = get_edu_params(edu_source)
    urls = {
        'primary': None,
        'fallback': None,
        'm3u8': None,
        'embed': f"https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1",
        'education': f"https://www.youtubeeducation.com/embed/{video_id}?{edu_params}"
    }

    try:
        # 直接のストリームURL取得 (360p 音声付き itag 18 を最優先)
        res = http_session.get(f"{STREAM_API}{video_id}", headers=get_random_headers(), timeout=(3, 6))
        if res.status_code == 200:
            data = res.json()
            formats = data.get('formats', [])

            # 360p 音声付き (itag 18) を優先
            for fmt in formats:
                if fmt.get('itag') == '18' or (fmt.get('height') == 360 and fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none'):
                    urls['primary'] = fmt.get('url')
                    break

            # 次点で 144p 音声付き (itag 17)
            if not urls['primary']:
                for fmt in formats:
                    if fmt.get('itag') == '17' or (fmt.get('height') == 144 and fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none'):
                        urls['primary'] = fmt.get('url')
                        break

            # それでもダメな場合は、何かしら動画が含まれるものを fallback に
            if not urls['primary']:
                for fmt in formats:
                    if fmt.get('url') and fmt.get('vcodec') != 'none':
                        urls['fallback'] = fmt.get('url')
                        break
    except:
        pass

    try:
        res = http_session.get(f"{M3U8_API}{video_id}", headers=get_random_headers(), timeout=(3, 6))
        if res.status_code == 200:
            data = res.json()
            m3u8_formats = data.get('m3u8_formats', [])
            if m3u8_formats:
                best = max(m3u8_formats, key=lambda x: int(x.get('resolution', '0x0').split('x')[-1] or 0))
                urls['m3u8'] = best.get('url')
    except:
        pass

    return urls

def get_comments(video_id):
    path = f"/comments/{urllib.parse.quote(video_id)}?hl=jp"
    data = request_invidious_api(path)

    if not data:
        return []

    comments = []
    for item in data.get('comments', []):
        thumbnails = item.get('authorThumbnails', [])
        author_thumbnail = thumbnails[-1].get('url', '') if thumbnails else ''
        comments.append({
            'author': item.get('author', ''),
            'authorThumbnail': author_thumbnail,
            'authorId': item.get('authorId', ''),
            'content': item.get('contentHtml', '').replace('\n', '<br>'),
            'likes': item.get('likeCount', 0),
            'published': item.get('publishedText', '')
        })

    return comments

def get_trending():
    cache_duration = 300
    current_time = time.time()

    if _trending_cache['data'] and (current_time - _trending_cache['timestamp']) < cache_duration:
        return _trending_cache['data']

    path = "/trending?region=JP"
    data = request_invidious_api(path, timeout=(5, 10))

    if data:
        results = []
        for item in data[:40]:
            length_seconds = item.get('lengthSeconds', 0)
            results.append({
                'id': item.get('videoId', ''),
                'title': item.get('title', ''),
                'author': item.get('author', ''),
                'authorId': item.get('authorId', ''),
                'thumbnail': f"https://i.ytimg.com/vi/{item.get('videoId', '')}/hqdefault.jpg",
                'published': item.get('publishedText', ''),
                'views': item.get('viewCountText', ''),
                'length': str(datetime.timedelta(seconds=length_seconds)) if length_seconds else ''
            })
        _trending_cache['data'] = results
        _trending_cache['timestamp'] = current_time
        return results

    return []

@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    query = request.args.get('q', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if query:
        search_results = get_youtube_search(query)
        return render_template('search.html', results=search_results, query=query, theme=theme, vc=vc)

    trending = get_trending()
    return render_template('home.html', trending=trending, theme=theme, vc=vc)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error='パスワードが違います')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/watch')
@login_required
def watch():
    video_id = request.args.get('v', '')
    edu_source = request.args.get('source', 'siawaseok')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not video_id:
        return redirect(url_for('index'))

    video_info = get_video_info(video_id)
    stream_urls = get_stream_url(video_id, edu_source)
    comments = get_comments(video_id)

    if not video_info:
        return render_template('watch.html', error='動画情報が取得できませんでした', theme=theme, vc=vc)

    return render_template('watch.html',
                         video=video_info,
                         streams=stream_urls,
                         comments=comments,
                         video_id=video_id,
                         edu_source=edu_source,
                         theme=theme,
                         vc=vc)

@app.route('/w')
@login_required
def watch_high():
    video_id = request.args.get('v', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not video_id:
        return redirect(url_for('index'))

    video_info = get_video_info(video_id)
    if not video_info:
        return render_template('watch.html', error='動画情報が取得できませんでした', theme=theme, vc=vc)

    return render_template('watch.html',
                         video=video_info,
                         video_id=video_id,
                         high_mode=True,
                         theme=theme,
                         vc=vc)

@app.route('/ume')
@login_required
def watch_embed():
    video_id = request.args.get('v', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not video_id:
        return redirect(url_for('index'))

    video_info = get_video_info(video_id)
    if not video_info:
        return render_template('watch.html', error='動画情報が取得できませんでした', theme=theme, vc=vc)

    return render_template('watch.html',
                         video=video_info,
                         video_id=video_id,
                         embed_mode=True,
                         theme=theme,
                         vc=vc)

@app.route('/edu')
@login_required
def watch_edu():
    video_id = request.args.get('v', '')
    edu_source = request.args.get('source', 'siawaseok')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not video_id:
        return redirect(url_for('index'))

    video_info = get_video_info(video_id)
    if not video_info:
        return render_template('watch.html', error='動画情報が取得できませんでした', theme=theme, vc=vc)

    edu_params = get_edu_params(edu_source)

    return render_template('watch.html',
                         video=video_info,
                         video_id=video_id,
                         edu_mode=True,
                         edu_params=edu_params,
                         edu_source=edu_source,
                         theme=theme,
                         vc=vc)

@app.route('/channel/<channel_id>')
@login_required
def channel(channel_id):
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    channel_info = get_channel_info(channel_id)

    if not channel_info:
        return render_template('channel.html', error='チャンネル情報が取得できませんでした', theme=theme, vc=vc)

    return render_template('channel.html', channel=channel_info, theme=theme, vc=vc)

@app.route('/channel/<channel_id>/videos')
@login_required
def channel_videos(channel_id):
    continuation = request.args.get('continuation', '')
    data = get_channel_videos(channel_id, continuation)
    return jsonify(data)

@app.route('/api/proxy-thumbnail')
def proxy_thumbnail():
    video_id = request.args.get('video_id', '')
    if not video_id:
        return jsonify({'error': 'video_id is required'}), 400

    current_time = time.time()
    if video_id in _thumbnail_cache:
        cached_data, timestamp = _thumbnail_cache[video_id]
        if current_time - timestamp < 3600:
            return Response(cached_data, mimetype='image/jpeg')

    # リストにある複数のURLを試す
    thumbnail_urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
    ]

    for url in thumbnail_urls:
        try:
            response = http_session.get(url, headers=get_random_headers(), timeout=5)
            if response.status_code == 200 and len(response.content) > 1000:
                if len(_thumbnail_cache) > 200:
                    _thumbnail_cache.clear()
                _thumbnail_cache[video_id] = (response.content, current_time)
                return Response(response.content, mimetype='image/jpeg')
        except:
            continue

    # フォールバック: デフォルトサムネイル
    try:
        fallback_url = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
        response = http_session.get(fallback_url, headers=get_random_headers(), timeout=5)
        if response.status_code == 200:
            return Response(response.content, mimetype='image/jpeg')
    except:
        pass
    
    return jsonify({'error': 'Thumbnail not found'}), 404

@app.route('/api/download/<video_id>')
@login_required
def api_download(video_id):
    format_type = request.args.get('format', 'video')
    quality = request.args.get('quality', '720')

    if format_type == 'audio':
        download_url = f"https://api.cobalt.tools/api/json"
        try:
            payload = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "vCodec": "h264",
                "vQuality": "720",
                "aFormat": "mp3",
                "isAudioOnly": True
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            res = http_session.post(download_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('url'):
                    return redirect(data['url'])
        except Exception as e:
            print(f"Cobalt API error: {e}")

        fallback_url = f"https://dl.y2mate.is/mates/convert?id={video_id}&format=mp3&quality=128"
        return redirect(fallback_url)
    else:
        download_url = f"https://api.cobalt.tools/api/json"
        try:
            payload = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "vCodec": "h264",
                "vQuality": quality,
                "aFormat": "mp3",
                "isAudioOnly": False
            }
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
            res = http_session.post(download_url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('url'):
                    return redirect(data['url'])
        except Exception as e:
            print(f"Cobalt API error: {e}")

        fallback_url = f"https://dl.y2mate.is/mates/convert?id={video_id}&format=mp4&quality={quality}"
        return redirect(fallback_url)

DOWNLOAD_DIR = tempfile.gettempdir()

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.strip()
    if len(filename) > 100:
        filename = filename[:100]
    return filename

def cleanup_old_downloads():
    try:
        current_time = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith('chocotube_') and (f.endswith('.mp4') or f.endswith('.mp3')):
                filepath = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(filepath):
                    file_age = current_time - os.path.getmtime(filepath)
                    if file_age > 600:
                        os.remove(filepath)
    except Exception as e:
        print(f"Cleanup error: {e}")

def get_yt_dlp_base_opts(output_template, cookie_file=None):
    """YouTube bot対策を回避するための共通yt-dlpオプションを返す"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': output_template,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        },
        'socket_timeout': 60,
        'retries': 5,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'age_limit': None,
        'geo_bypass': True,
        'geo_bypass_country': 'JP',
    }
    if cookie_file:
        opts['cookiefile'] = cookie_file
    return opts

def create_youtube_cookies(cookie_file):
    """YouTube用のcookieファイルを作成する"""
    cookies_content = """# Netscape HTTP Cookie File
.youtube.com    TRUE    /       TRUE    2147483647      CONSENT PENDING+987
.youtube.com    TRUE    /       TRUE    2147483647      SOCS    CAESEwgDEgk2MjQyNTI1NzkaAmphIAEaBgiA_LyuBg
.youtube.com    TRUE    /       TRUE    2147483647      PREF    tz=Asia.Tokyo&hl=ja&gl=JP
.youtube.com    TRUE    /       TRUE    2147483647      GPS     1
.youtube.com    TRUE    /       TRUE    2147483647      YSC     DwKYllHNwuw
.youtube.com    TRUE    /       TRUE    2147483647      VISITOR_INFO1_LIVE      random_visitor_id
"""
    with open(cookie_file, 'w') as f:
        f.write(cookies_content)

@app.route('/api/internal-download/<video_id>')
@login_required
def api_internal_download(video_id):
    format_type = request.args.get('format', 'mp4')
    quality = request.args.get('quality', '360')

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    cleanup_old_downloads()

    unique_id = f"{video_id}_{int(time.time())}"
    cookie_file = os.path.join(DOWNLOAD_DIR, f'cookies_{unique_id}.txt')

    try:
        create_youtube_cookies(cookie_file)

        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiefile': cookie_file,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br',
            },
            'socket_timeout': 60,
            'retries': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web'],
                    'player_skip': ['webpage', 'configs'],
                }
            },
            'age_limit': None,
            'geo_bypass': True,
            'geo_bypass_country': 'JP',
        }

        if format_type == 'mp3':
            output_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.mp3')
            ydl_opts = {
                **base_opts,
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            output_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.mp4')
            # 360p 音声付き (itag 18) を最優先にするフォーマット指定
            format_string = '18/bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/best'
            ydl_opts = {
                **base_opts,
                'format': format_string,
                'outtmpl': os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.%(ext)s'),
                'merge_output_format': 'mp4',
            }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            title = sanitize_filename(info.get('title', video_id) if info else video_id)

        if os.path.exists(cookie_file):
            os.remove(cookie_file)

        # 送信後にファイルを削除する
        @after_this_request
        def remove_file(response):
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
                # cookie_fileも再確認して削除
                if os.path.exists(cookie_file):
                    os.remove(cookie_file)
            except Exception as e:
                print(f"Error removing download file: {e}")
            return response

        if format_type == 'mp3':
            if os.path.exists(output_path):
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name=f"{title}.mp3",
                    mimetype='audio/mpeg'
                )
            for ext in ['mp3', 'm4a', 'webm', 'opus']:
                check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.{ext}')
                if os.path.exists(check_path):
                    return send_file(
                        check_path,
                        as_attachment=True,
                        download_name=f"{title}.mp3",
                        mimetype='audio/mpeg'
                    )
        else:
            if os.path.exists(output_path):
                return send_file(
                    output_path,
                    as_attachment=True,
                    download_name=f"{title}.mp4",
                    mimetype='video/mp4'
                )
            for ext in ['mp4', 'mkv', 'webm']:
                check_path = os.path.join(DOWNLOAD_DIR, f'chocotube_{unique_id}.{ext}')
                if os.path.exists(check_path):
                    return send_file(
                        check_path,
                        as_attachment=True,
                        download_name=f"{title}.mp4",
                        mimetype='video/mp4'
                    )

        return jsonify({
            'success': False,
            'error': 'ファイルのダウンロードに失敗しました'
        }), 500

    except Exception as e:
        print(f"Internal download error: {e}")
        if os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass
        return jsonify({
            'success': False,
            'error': f'ダウンロードエラー: {str(e)}'
        }), 500

@app.route('/api/stream/<video_id>')
@login_required
def api_stream(video_id):
    try:
        stream_url = f"https://siawaseok.duckdns.org/api/stream/{video_id}/type2"
        res = http_session.get(stream_url, headers=get_random_headers(), timeout=15)
        if res.status_code == 200:
            data = res.json()
            return jsonify(data)
        else:
            return jsonify({'error': 'ストリームデータの取得に失敗しました'}), res.status_code
    except Exception as e:
        print(f"Stream API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/lite-download/<video_id>')
@login_required
def api_lite_download(video_id):
    format_type = request.args.get('format', 'mp4')
    quality = request.args.get('quality', '360')

    try:
        stream_url = f"https://siawaseok.duckdns.org/api/stream/{video_id}/type2"
        res = http_session.get(stream_url, headers=get_random_headers(), timeout=15)

        if res.status_code != 200:
            return jsonify({'error': 'ストリームデータの取得に失敗しました', 'success': False}), 500

        data = res.json()
        videourl = data.get('videourl', {})

        if format_type == 'mp3' or format_type == 'm4a':
            audio_url = None
            for q in ['144p', '240p', '360p', '480p', '720p']:
                if q in videourl and videourl[q].get('audio', {}).get('url'):
                    audio_url = videourl[q]['audio']['url']
                    break

            if audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'format': 'm4a',
                    'quality': 'audio',
                    'actual_format': 'm4a'
                })
            else:
                return jsonify({'error': '音声URLが見つかりませんでした', 'success': False}), 404
        elif format_type == 'mp4':
            quality_order = [quality + 'p', '360p', '480p', '720p', '240p', '144p']
            video_url = None
            actual_quality = None

            for q in quality_order:
                if q in videourl and videourl[q].get('video', {}).get('url'):
                    video_url = videourl[q]['video']['url']
                    actual_quality = q
                    break

            if video_url:
                return jsonify({
                    'success': True,
                    'url': video_url,
                    'format': 'mp4',
                    'quality': actual_quality,
                    'actual_format': 'mp4'
                })
            else:
                return jsonify({'error': '動画URLが見つかりませんでした', 'success': False}), 404
        else:
            return jsonify({'error': '無効なフォーマットです', 'success': False}), 400

    except Exception as e:
        print(f"Lite download error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/audio-stream/<video_id>')
@login_required
def api_audio_stream(video_id):
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

            if not info:
                return jsonify({'success': False, 'error': '動画情報が取得できませんでした'}), 404

            audio_url = info.get('url')

            if not audio_url:
                formats = info.get('formats', [])
                for fmt in formats:
                    if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                        audio_url = fmt.get('url')
                        if audio_url and 'googlevideo.com' in audio_url:
                            break

                if not audio_url:
                    for fmt in formats:
                        if fmt.get('acodec') != 'none':
                            url = fmt.get('url', '')
                            if 'googlevideo.com' in url:
                                audio_url = url
                                break

            if audio_url and 'googlevideo.com' in audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'title': info.get('title', '') if info else '',
                    'format': 'audio',
                    'source': 'googlevideo'
                })
            elif audio_url:
                return jsonify({
                    'success': True,
                    'url': audio_url,
                    'title': info.get('title', '') if info else '',
                    'format': 'audio',
                    'source': 'other'
                })
            else:
                return jsonify({'success': False, 'error': '音声URLが見つかりませんでした'}), 404

    except Exception as e:
        print(f"Audio stream error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/thumbnail-download/<video_id>')
@login_required
def api_thumbnail_download(video_id):
    quality = request.args.get('quality', 'hq')

    quality_map = {
        'max': 'maxresdefault',
        'sd': 'sddefault',
        'hq': 'hqdefault',
        'mq': 'mqdefault',
        'default': 'default'
    }

    thumbnail_name = quality_map.get(quality, 'hqdefault')
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/{thumbnail_name}.jpg"

    try:
        res = http_session.get(thumbnail_url, headers=get_random_headers(), timeout=10)

        if res.status_code == 200 and len(res.content) > 1000:
            response = Response(res.content, mimetype='image/jpeg')
            response.headers['Content-Disposition'] = f'attachment; filename="{video_id}_{thumbnail_name}.jpg"'
            return response

        if quality != 'hq':
            fallback_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            res = http_session.get(fallback_url, headers=get_random_headers(), timeout=10)
            if res.status_code == 200:
                response = Response(res.content, mimetype='image/jpeg')
                response.headers['Content-Disposition'] = f'attachment; filename="{video_id}_hqdefault.jpg"'
                return response

        return jsonify({'error': 'サムネイルの取得に失敗しました', 'success': False}), 404

    except Exception as e:
        print(f"Thumbnail download error: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/playlist')
@login_required
def playlist_page():
    playlist_id = request.args.get('list', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if not playlist_id:
        return redirect(url_for('index'))

    playlist_info = get_playlist_info(playlist_id)

    if not playlist_info:
        return render_template('playlist.html', playlist=None, videos=[], theme=theme, vc=vc)

    return render_template('playlist.html',
                         playlist=playlist_info,
                         videos=playlist_info.get('videos', []),
                         theme=theme,
                         vc=vc)

@app.route('/thumbnail')
def thumbnail():
    video_id = request.args.get('v', '')
    if not video_id:
        return '', 404

    current_time = time.time()
    cache_key = video_id
    if cache_key in _thumbnail_cache:
        cached_data, cached_time = _thumbnail_cache[cache_key]
        if current_time - cached_time < 3600:
            response = Response(cached_data, mimetype='image/jpeg')
            response.headers['Cache-Control'] = 'public, max-age=3600'
            return response

    try:
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        res = http_session.get(url, headers=get_random_headers(), timeout=3)
        if len(_thumbnail_cache) > 500:
            oldest_key = min(_thumbnail_cache.keys(), key=lambda k: _thumbnail_cache[k][1])
            del _thumbnail_cache[oldest_key]
        _thumbnail_cache[cache_key] = (res.content, current_time)
        response = Response(res.content, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return response
    except:
        return '', 404

@app.route('/suggest')
def suggest():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    try:
        url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&hl=ja&q={urllib.parse.quote(query)}"
        res = http_session.get(url, headers=get_random_headers(), timeout=3)
        if res.status_code == 200:
            # 形式: window.google.ac.h(["query",[["suggest1",0],["suggest2",0],...]])
            content = res.text
            start = content.find('(') + 1
            end = content.rfind(')')
            if start > 0 and end > 0:
                data = json.loads(content[start:end])
                suggestions = [item[0] for item in data[1]]
                return jsonify(suggestions)
    except:
        pass
    return jsonify([])

@app.route('/history')
@login_required
def history():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('history.html', theme=theme, vc=vc)

@app.route('/favorite')
@login_required
def favorite():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('favorite.html', theme=theme, vc=vc)

@app.route('/subscribed-channels')
@login_required
def subscribed_channels():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('subscribed-channels.html', theme=theme, vc=vc)

@app.route('/setting')
def setting():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('setting.html', theme=theme, vc=vc)

@app.route('/help')
def help():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('help.html', theme=theme, vc=vc)

@app.route('/blog')
def blog():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('blog.html', theme=theme, vc=vc)

@app.route('/chat')
@login_required
def chat():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('chat.html', theme=theme, vc=vc)

@app.route('/tool')
def tool():
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('tool.html', theme=theme, vc=vc)

@app.route('/downloader')
@login_required
def downloader():
    video_id = request.args.get('v', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    return render_template('downloader.html', video_id=video_id, theme=theme, vc=vc)

@app.route('/proxy')
def proxy():
    url = request.args.get('url', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    if not url:
        return render_template('proxy.html', theme=theme, vc=vc)

    try:
        if not url.startswith('http'):
            url = 'https://' + url
        res = requests.get(url, headers=get_random_headers(), timeout=10)
        return Response(res.content, content_type=res.headers.get('Content-Type'))
    except Exception as e:
        return f"Error: {e}", 500

@app.route('/getcode')
def getcode():
    url = request.args.get('url', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')
    if not url:
        return render_template('getcode.html', theme=theme, vc=vc)

    try:
        if not url.startswith('http'):
            url = 'https://' + url
        res = requests.get(url, headers=get_random_headers(), timeout=10)
        return render_template('getcode.html', code=res.text, url=url, theme=theme, vc=vc)
    except Exception as e:
        return render_template('getcode.html', error=str(e), theme=theme, vc=vc)

@app.route('/music')
@login_required
def music():
    query = request.args.get('q', '')
    theme = request.cookies.get('theme', 'dark')
    vc = request.cookies.get('vc', '1')

    if query:
        search_results = get_youtube_search(query + " music")
        return render_template('music.html', results=search_results, query=query, theme=theme, vc=vc)

    trending = get_trending()
    return render_template('music.html', trending=trending, theme=theme, vc=vc)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
