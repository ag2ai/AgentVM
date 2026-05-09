# Core dependencies for file conversion
pip install 'mammoth==1.9.0' || true
pip install 'markdownify' || true
pip install 'beautifulsoup4' || true
pip install 'pandas==2.2.3' || true
pip install 'python-pptx==1.0.2' || true
pip install 'puremagic==1.29' || true
pip install 'youtube_transcript_api' || true
pip install 'openpyxl==3.1.5' || true
pip install 'pdfminer==20191125' || true
pip install 'pdfminer-six==20250506' || true
pip install 'requests' || true
pip install 'chunkr-ai' || true
pip install 'SpeechRecognition==3.14.2' || true
pip install 'pydub==0.25.1' || true
pip install 'soundfile==0.13.1' || true
pip install 'serpapi==0.1.5' || true
pip install 'markdownify==0.14.0' || true
pip install 'pathvalidate==3.2.3' || true
pip install 'google-search-results' || true

# Install ffmpeg system binary (required for pydub)
# sudo apt-get update && sudo apt-get install -y ffmpeg || true

# Optional: Audio transcription (uncomment if needed)
# pip install 'pydub==0.25.1' || true
# pip install 'openai-whisper==20240930' || true

# Optional: AI-powered image descriptions (uncomment if needed)
# pip install 'openai==1.78.0' || true