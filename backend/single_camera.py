"""
Video Subtitle Processor - Backend API (Standalone Version)
视频字幕处理器后端API - 无需whisper版本
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import uuid
import json
import subprocess
from datetime import datetime
from werkzeug.utils import secure_filename

# 尝试导入whisper，如果失败则使用模拟模式
try:
    import whisper

    WHISPER_AVAILABLE = True
    print("[INFO] Whisper loaded successfully")
except ImportError:
    WHISPER_AVAILABLE = False
    print("[INFO] Whisper not installed, using mock mode")

app = Flask(__name__)
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": [
                "http://localhost:8080",
                "http://127.0.0.1:8080",
                "http://localhost:5000",
                "file://*",
            ],
            "methods": ["GET", "POST", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type"],
        }
    },
)

# Configuration - 使用上级目录的路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global model cache
whisper_model = None


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_audio(video_path, audio_path):
    """Extract audio from video using ffmpeg"""
    command = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        audio_path,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
        return True
    except:
        return False


def get_video_duration(video_path):
    """Get video duration using ffprobe"""
    try:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        return float(result.stdout.strip())
    except:
        return 60  # Default 60 seconds


def generate_mock_subtitles(duration=60):
    """Generate mock subtitle data in Simplified Chinese"""
    mock_texts = [
        "大家好，欢迎来到本次会议",
        "今天我们要讨论的是产品设计方向",
        "首先请产品经理来介绍一下",
        "我们的目标是在第三季度完成这个版本",
        "用户体验是我们最关注的点",
        "我们需要更多的用户调研数据",
        "竞品分析已经做完了，我稍后分享",
        "技术实现上有什么难点吗",
        "主要是性能优化方面的挑战",
        "我们可以分阶段来实现这个功能",
        "先做个最小可行版本测试一下市场反应",
        "好的，那我们定一下时间节点",
        "下周一开始开发，月底完成初版",
        "测试周期需要预留两周时间",
        "没问题，那我们今天就到这里",
        "谢谢大家，会议结束",
    ]

    speakers = ["SPEAKER_00"]
    subtitles = []

    num_subtitles = min(int(duration / 4), len(mock_texts))
    interval = duration / num_subtitles if duration > 0 else 4

    for i in range(num_subtitles):
        start = i * interval
        end = start + interval - 0.5

        subtitles.append(
            {
                "id": i,
                "start": round(start, 2),
                "end": round(end, 2),
                "text": mock_texts[i % len(mock_texts)],
                "speaker": speakers[0],  # Always use 1 speaker for mock data
            }
        )

    return subtitles


def get_whisper_model(model_size="base"):
    """Load Whisper model (cached)"""
    global whisper_model
    if whisper_model is None and WHISPER_AVAILABLE:
        print(f"Loading Whisper model: {model_size}")
        whisper_model = whisper.load_model(model_size)
    return whisper_model


def transcribe_audio(audio_path, model_size="base"):
    """Transcribe audio using Whisper with Simplified Chinese"""
    if not WHISPER_AVAILABLE:
        return None
    try:
        model = get_whisper_model(model_size)
        # Force Simplified Chinese
        result = model.transcribe(
            audio_path,
            language="zh",
            task="transcribe",
            initial_prompt="以下是普通话的句子。",
        )
        return result
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "whisper_loaded": WHISPER_AVAILABLE and whisper_model is not None,
            "mode": "whisper" if WHISPER_AVAILABLE else "mock",
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/upload", methods=["POST"])
def upload_video():
    """Upload video file"""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type"}), 400

    # Generate unique ID
    file_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)

    # Save file
    video_path = os.path.join(UPLOAD_FOLDER, f"{file_id}_{filename}")
    file.save(video_path)

    # Get duration
    duration = get_video_duration(video_path)

    return jsonify(
        {
            "success": True,
            "file_id": file_id,
            "filename": filename,
            "video_path": video_path,
            "duration": duration,
        }
    )


@app.route("/api/process", methods=["POST"])
def process_video():
    """Process video: extract audio and generate subtitles"""
    data = request.json
    video_path = data.get("video_path")
    file_id = data.get("file_id")

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video file not found"}), 404

    try:
        duration = get_video_duration(video_path)

        if WHISPER_AVAILABLE:
            # Try to use real Whisper transcription
            audio_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_audio.wav")

            if extract_audio(video_path, audio_path):
                transcription = transcribe_audio(audio_path)

                if transcription and "segments" in transcription:
                    subtitles = []
                    for i, seg in enumerate(transcription["segments"]):
                        subtitles.append(
                            {
                                "id": i,
                                "start": seg["start"],
                                "end": seg["end"],
                                "text": seg["text"].strip(),
                                "speaker": "SPEAKER_00",  # Single speaker for now
                            }
                        )
                else:
                    subtitles = generate_mock_subtitles(duration)
            else:
                subtitles = generate_mock_subtitles(duration)
        else:
            # Use mock subtitles
            subtitles = generate_mock_subtitles(duration)

        # Save subtitles
        json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(subtitles, f, ensure_ascii=False, indent=2)

        unique_speakers = list(set(s["speaker"] for s in subtitles))

        return jsonify(
            {
                "success": True,
                "file_id": file_id,
                "subtitles": subtitles,
                "stats": {
                    "total_duration": duration,
                    "subtitle_count": len(subtitles),
                    "speaker_count": len(unique_speakers),
                    "speakers": unique_speakers,
                },
            }
        )

    except Exception as e:
        print(f"Processing error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/subtitles/<file_id>", methods=["GET"])
def get_subtitles(file_id):
    """Get subtitles for a file"""
    json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "Subtitles not found"}), 404

    with open(json_path, "r", encoding="utf-8") as f:
        subtitles = json.load(f)

    return jsonify({"success": True, "subtitles": subtitles})


@app.route("/api/search", methods=["POST"])
def search_subtitles():
    """Search subtitles by keyword"""
    data = request.json
    file_id = data.get("file_id")
    keyword = data.get("keyword", "").lower()

    if not file_id or not keyword:
        return jsonify({"error": "Missing file_id or keyword"}), 400

    json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "Subtitles not found"}), 404

    with open(json_path, "r", encoding="utf-8") as f:
        subtitles = json.load(f)

    results = []
    for sub in subtitles:
        if keyword in sub["text"].lower():
            results.append({**sub, "match_score": 1.0})

    return jsonify(
        {"success": True, "keyword": keyword, "results": results, "total": len(results)}
    )


@app.route("/api/batch-search", methods=["POST"])
def batch_search():
    """Batch search multiple quotes"""
    data = request.json
    file_id = data.get("file_id")
    quotes = data.get("quotes", [])

    if not file_id or not quotes:
        return jsonify({"error": "Missing file_id or quotes"}), 400

    json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "Subtitles not found"}), 404

    with open(json_path, "r", encoding="utf-8") as f:
        subtitles = json.load(f)

    results = []
    for i, quote in enumerate(quotes):
        quote_clean = quote.strip()
        if not quote_clean:
            continue

        best_match = None
        best_score = 0

        for sub in subtitles:
            if quote_clean.lower() in sub["text"].lower():
                score = 1.0
            else:
                quote_words = set(quote_clean.lower().split())
                text_words = set(sub["text"].lower().split())
                intersection = quote_words & text_words
                score = len(intersection) / len(quote_words) if quote_words else 0

            if score > best_score:
                best_score = score
                best_match = sub

        results.append(
            {
                "index": i + 1,
                "query": quote_clean,
                "found": best_score > 0.2,
                "subtitle": best_match,
                "score": round(best_score, 2),
            }
        )

    return jsonify(
        {
            "success": True,
            "results": results,
            "total": len(results),
            "found": len([r for r in results if r["found"]]),
        }
    )


@app.route("/api/export/<file_id>", methods=["GET"])
def export_subtitles(file_id):
    """Export subtitles in various formats"""
    format_type = request.args.get("format", "json")

    json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "Subtitles not found"}), 404

    with open(json_path, "r", encoding="utf-8") as f:
        subtitles = json.load(f)

    if format_type == "srt":
        srt_content = []
        for i, sub in enumerate(subtitles, 1):
            start = format_srt_time(sub["start"])
            end = format_srt_time(sub["end"])
            srt_content.append(f"{i}\n{start} --> {end}\n{sub['text']}\n")

        output_path = os.path.join(OUTPUT_FOLDER, f"{file_id}.srt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_content))

        return send_file(
            output_path, as_attachment=True, download_name=f"subtitles_{file_id}.srt"
        )

    elif format_type == "md":
        md_content = f"# 字幕记录\n\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        for sub in subtitles:
            md_content += (
                f"- **{format_time(sub['start'])}** [{sub['speaker']}]: {sub['text']}\n"
            )

        output_path = os.path.join(OUTPUT_FOLDER, f"{file_id}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        return send_file(
            output_path, as_attachment=True, download_name=f"subtitles_{file_id}.md"
        )

    elif format_type == "fcpxml":
        fcpxml_content = generate_fcpxml(subtitles, file_id)
        output_path = os.path.join(OUTPUT_FOLDER, f"{file_id}.fcpxml")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(fcpxml_content)

        return send_file(
            output_path, as_attachment=True, download_name=f"timeline_{file_id}.fcpxml"
        )

    else:
        return send_file(
            json_path, as_attachment=True, download_name=f"subtitles_{file_id}.json"
        )


def generate_fcpxml(subtitles, file_id):
    """Generate FCPXML format for Final Cut Pro"""
    if not subtitles:
        return ""

    total_duration = int(subtitles[-1]["end"]) if subtitles else 0

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
    <resources>
        <asset id="r1" name="video_{file_id}" uid="{uuid.uuid4()}" start="0s" duration="{total_duration}s">
            <media-rep kind="original-media" src="file://uploads/{file_id}_video.mp4"/>
        </asset>
    </resources>
    <sequence duration="{total_duration}s" format="r1">
        <spine>
"""

    for i, sub in enumerate(subtitles):
        duration = sub["end"] - sub["start"]
        xml += f"""            <clip name="字幕{i + 1}" offset="{sub["start"]}s" duration="{duration}s">
                <asset-clip ref="r1" offset="{sub["start"]}s" duration="{duration}s"/>
            </clip>
"""

    xml += """        </spine>
    </sequence>
</fcpxml>"""

    return xml


def format_time(seconds):
    """Format seconds to MM:SS.ms"""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 100)
    return f"{mins:02d}:{secs:02d}.{ms:02d}"


def format_srt_time(seconds):
    """Format seconds to SRT time format"""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}"


@app.route("/api/cleanup/<file_id>", methods=["DELETE"])
def cleanup(file_id):
    """Clean up uploaded and generated files"""
    try:
        for f in os.listdir(UPLOAD_FOLDER):
            if f.startswith(file_id):
                os.remove(os.path.join(UPLOAD_FOLDER, f))

        for f in os.listdir(OUTPUT_FOLDER):
            if f.startswith(file_id):
                os.remove(os.path.join(OUTPUT_FOLDER, f))

        return jsonify({"success": True, "message": "Cleanup completed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Serve static files (frontend)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "single")

@app.route("/", methods=["GET"])
def serve_index():
    """Serve the frontend index.html"""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>", methods=["GET"])
def serve_static(path):
    """Serve static files"""
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.exists(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    # 也检查 multi 目录
    multi_path = os.path.join(BASE_DIR, "frontend", "multi", path)
    if os.path.exists(multi_path):
        return send_from_directory(os.path.join(BASE_DIR, "frontend", "multi"), path)
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    print("=" * 60)
    print("Video Subtitle Processor API")
    print("=" * 60)
    print(f"Mode: {'Whisper' if WHISPER_AVAILABLE else 'Mock (Demo)'}")
    print("URL: http://localhost:5000")
    print("API: http://localhost:5000/api/health")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5000, debug=True)
