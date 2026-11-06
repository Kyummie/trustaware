import re
from youtube_transcript_api import YouTubeTranscriptApi


def get_video_id(url):
    match = re.search(r'(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})', url or '')
    return match.group(1) if match else None


def get_transcript(url):
    """Return the video's English captions as plain text, or None if unavailable."""
    video_id = get_video_id(url)
    if not video_id:
        return None
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=['en'])
        return ' '.join(snippet.text for snippet in fetched)
    except Exception:
        return None