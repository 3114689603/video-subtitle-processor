"""
视频字幕处理器 - 统一后端 (单视频 + 多机位)
整合两个版本的功能到一个服务中
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import uuid
import json
import subprocess
import xml.etree.ElementTree as ET
import re
import difflib
import threading
from datetime import datetime
from werkzeug.utils import secure_filename
from typing import List, Dict, Any, Optional, Callable

# 尝试导入依赖
try:
    import whisper

    WHISPER_AVAILABLE = True
    print("[INFO] Whisper loaded successfully")
except ImportError:
    WHISPER_AVAILABLE = False
    print("[WARNING] Whisper not installed")

try:
    from speaker_diarization import create_pipeline, assign_speakers_to_subtitles

    DIARIZATION_AVAILABLE = True
    print("[INFO] Speaker diarization module loaded")
except ImportError as e:
    DIARIZATION_AVAILABLE = False
    print(f"[WARNING] Speaker diarization not available: {e}")

app = Flask(__name__)
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": ["*"],
            "methods": ["GET", "POST", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type"],
        }
    },
)

# Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
CACHE_DIR = r"C:\Users\36961\.cache\whisper"
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# FFmpeg 路径
FFMPEG_PATH = "ffmpeg"
FFPROBE_PATH = "ffprobe"

def check_ffmpeg_available() -> bool:
    """检查 FFmpeg 是否可用"""
    try:
        subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global model cache
whisper_model = None
diarization_pipeline = None

# Storage
single_projects: Dict[str, Dict] = {}  # 单视频项目
multi_projects: Dict[str, Dict] = {}  # 多机位项目

# Global progress tracking: project_id -> {"percent": int, "status": str, "error": str|None}
transcribe_progress = {}

# Global subtitle storage: project_id -> List[Dict]
subtitle_storage = {}

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "mxf"}


def calculate_similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度 (0-100)"""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_video_info(video_path: str) -> Dict:
    """获取视频信息"""
    try:
        cmd_video = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,r_frame_rate",
            "-of",
            "json",
            video_path,
        ]
        result_video = subprocess.run(cmd_video, capture_output=True, text=True)
        video_info = json.loads(result_video.stdout)

        cmd_format = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "json",
            video_path,
        ]
        result_format = subprocess.run(cmd_format, capture_output=True, text=True)
        format_info = json.loads(result_format.stdout)

        return {
            "width": video_info.get("streams", [{}])[0].get("width", 1920),
            "height": video_info.get("streams", [{}])[0].get("height", 1080),
            "duration": float(format_info.get("format", {}).get("duration", 0)),
            "bitrate": int(format_info.get("format", {}).get("bit_rate", 0)),
            "codec": video_info.get("streams", [{}])[0].get("codec_name", "h264"),
        }
    except Exception as e:
        print(f"[ERROR] Failed to get video info: {e}")
        return {
            "width": 1920,
            "height": 1080,
            "duration": 0,
            "bitrate": 0,
            "codec": "h264",
        }


def extract_audio(video_path: str, audio_path: str) -> bool:
    """提取音频"""
    try:
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
        subprocess.run(command, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"[ERROR] Audio extraction failed: {e}")
        return False


def transcribe_with_whisper(
    audio_path: str,
    model_size: str = "base",
    project_id: str = "",
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[Dict]:
    """Whisper 转录，支持进度回调"""
    global whisper_model

    if not WHISPER_AVAILABLE:
        return []

    try:
        if whisper_model is None:
            print(f"[INFO] Loading Whisper model: {model_size}")
            os.environ["WHISPER_CACHE_DIR"] = CACHE_DIR
            whisper_model = whisper.load_model(model_size, download_root=CACHE_DIR)

        result = whisper_model.transcribe(
            audio_path,
            language="zh",
            task="transcribe",
            initial_prompt="以下是普通话的句子。",
            verbose=False,
        )

        try:
            import zhconv
        except ImportError:
            zhconv = None

        segments = result.get("segments", [])
        total_segments = len(segments)
        subtitles = []
        for i, seg in enumerate(segments):
            text = seg["text"].strip()
            if zhconv:
                text = zhconv.convert(text, "zh-cn")
            subtitles.append(
                {
                    "id": i,
                    "start": round(seg["start"], 3),
                    "end": round(seg["end"], 3),
                    "text": text,
                    "confidence": seg.get("avg_logprob", -1.0),
                }
            )
            # 每处理 10% 的片段报告一次进度
            if progress_callback and total_segments > 0:
                percent = int((i + 1) / total_segments * 100)
                if percent % 10 == 0 or i == total_segments - 1:
                    progress_callback(percent)

        # 智能拆分长字幕
        subtitles = split_subtitles(subtitles)

        return subtitles
    except Exception as e:
        print(f"[ERROR] Transcription failed: {e}")
        return []


def split_subtitles(subtitles: List[Dict]) -> List[Dict]:
    """
    智能拆分长字幕：
    1. 按标点符号（，、,）优先拆分
    2. 超过20字或5秒的片段强制按字数切分
    3. 1-2字的碎片合并到下一条（最后一条则合并到上一条）
    4. 按字数比例重新分配时间
    """
    if not subtitles:
        return []

    result = []
    for seg in subtitles:
        text = seg.get("text", "").strip()
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        duration = end - start

        if not text:
            continue

        # 第一遍：按标点拆分
        parts = re.split(r"([，、,])", text)
        chunks = []
        current = ""
        for part in parts:
            if part in "，、,":
                current += part
                chunks.append(current)
                current = ""
            else:
                current += part
        if current:
            chunks.append(current)

        # 清理并过滤空片段
        chunks = [c.strip() for c in chunks if c.strip()]

        # 第二遍：强制按字数/时长切分
        final_chunks = []
        for chunk in chunks:
            chunk_duration = duration * (len(chunk) / max(len(text), 1))
            if len(chunk) > 20 or chunk_duration > 5.0:
                # 强制切分：CJK每20字，拉丁每8词
                words = chunk.split()
                if len(words) > 1 and all(ord(c) < 128 for c in chunk):
                    # 拉丁文本按词切分
                    sub_chunks = []
                    current_words = []
                    for word in words:
                        current_words.append(word)
                        if len(current_words) >= 8:
                            sub_chunks.append(" ".join(current_words))
                            current_words = []
                    if current_words:
                        sub_chunks.append(" ".join(current_words))
                    final_chunks.extend(sub_chunks)
                else:
                    # CJK按字切分，每20字
                    sub_chunks = []
                    for i in range(0, len(chunk), 20):
                        sub_chunks.append(chunk[i : i + 20])
                    final_chunks.extend(sub_chunks)
            else:
                final_chunks.append(chunk)

        # 第三遍：合并1-2字碎片
        merged = []
        for i, chunk in enumerate(final_chunks):
            if len(chunk) <= 2:
                if i == len(final_chunks) - 1:
                    # 最后一条，合并到前一条
                    if merged:
                        merged[-1] += chunk
                    else:
                        merged.append(chunk)
                else:
                    # 合并到下一条
                    if i + 1 < len(final_chunks):
                        final_chunks[i + 1] = chunk + final_chunks[i + 1]
            else:
                merged.append(chunk)

        # 重新分配时间
        total_chars = sum(len(c) for c in merged)
        current_start = start
        for i, chunk in enumerate(merged):
            if total_chars > 0:
                chunk_duration = duration * (len(chunk) / total_chars)
            else:
                chunk_duration = duration / max(len(merged), 1)
            chunk_end = round(current_start + chunk_duration, 3)
            # 确保最后一条不超出总时长
            if i == len(merged) - 1:
                chunk_end = end
            result.append(
                {
                    "id": len(result),
                    "start": round(current_start, 3),
                    "end": chunk_end,
                    "text": chunk,
                    "confidence": seg.get("confidence", -1.0),
                }
            )
            current_start = chunk_end

    return result


def perform_speaker_diarization(audio_path: str, num_speakers: int = 2) -> List[Dict]:
    """说话人分离"""
    global diarization_pipeline

    if not DIARIZATION_AVAILABLE:
        return []

    try:
        if diarization_pipeline is None:
            print(f"[INFO] Initializing speaker diarization")
            diarization_pipeline = create_pipeline(num_speakers)

        segments = diarization_pipeline(audio_path, num_speakers)
        return segments
    except Exception as e:
        print(f"[ERROR] Diarization failed: {e}")
        return []


# ==================== 单视频 API ====================


@app.route("/api/health", methods=["GET"])
def health_check():
    """健康检查"""
    return jsonify(
        {
            "status": "healthy",
            "ffmpeg": check_ffmpeg_available(),
            "whisper": WHISPER_AVAILABLE,
            "diarization_available": DIARIZATION_AVAILABLE,
            "mode": "unified",
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/upload", methods=["POST"])
def upload_single():
    """单视频上传"""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file"}), 400

    file_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    video_path = os.path.join(UPLOAD_FOLDER, f"single_{file_id}_{filename}")
    file.save(video_path)

    video_info = get_video_info(video_path)

    single_projects[file_id] = {
        "id": file_id,
        "video_path": video_path,
        "filename": filename,
        **video_info,
    }

    return jsonify(
        {
            "success": True,
            "file_id": file_id,
            "filename": filename,
            "video_path": video_path,
            "duration": video_info["duration"],
        }
    )


@app.route("/api/process", methods=["POST"])
def process_single():
    """单视频处理（异步后台线程）"""
    data = request.json
    video_path = data.get("video_path")
    file_id = data.get("file_id")

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video file not found"}), 404

    # 初始化进度
    transcribe_progress[file_id] = {"percent": 0, "status": "running", "error": None}

    def _do_transcribe():
        try:
            # 提取音频
            audio_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_audio.wav")
            if not extract_audio(video_path, audio_path):
                transcribe_progress[file_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Audio extraction failed",
                }
                return

            # 进度回调
            def progress_callback(p):
                transcribe_progress[file_id]["percent"] = p

            # 转录
            subtitles = transcribe_with_whisper(
                audio_path, "base", file_id, progress_callback
            )

            if not subtitles:
                transcribe_progress[file_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Transcription failed",
                }
                return

            # 说话人分离
            speaker_segments = perform_speaker_diarization(audio_path, 2)
            subtitles = assign_speakers_to_subtitles(subtitles, speaker_segments)

            # 保存到文件
            json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(subtitles, f, ensure_ascii=False, indent=2)

            # 存储到内存
            single_projects[file_id]["subtitles"] = subtitles
            subtitle_storage[file_id] = subtitles

            # 清理
            if os.path.exists(audio_path):
                os.remove(audio_path)

            transcribe_progress[file_id] = {
                "percent": 100,
                "status": "done",
                "error": None,
            }
        except Exception as e:
            transcribe_progress[file_id] = {
                "percent": 0,
                "status": "error",
                "error": str(e),
            }

    # 启动后台线程
    thread = threading.Thread(target=_do_transcribe, daemon=True)
    thread.start()

    return jsonify({"status": "started", "project_id": file_id})


@app.route("/api/export/<file_id>", methods=["GET"])
def export_single(file_id):
    """导出单视频字幕"""
    format_type = request.args.get("format", "json")

    json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
    if not os.path.exists(json_path):
        return jsonify({"error": "Subtitles not found"}), 404

    with open(json_path, "r", encoding="utf-8") as f:
        subtitles = json.load(f)

    if format_type == "srt":
        content = []
        for i, sub in enumerate(subtitles, 1):
            start = format_srt_time(sub["start"])
            end = format_srt_time(sub["end"])
            content.append(f"{i}\n{start} --> {end}\n{sub['text']}\n")

        output_path = os.path.join(OUTPUT_FOLDER, f"{file_id}.srt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(content))

        return send_file(
            output_path, as_attachment=True, download_name=f"subtitles_{file_id}.srt"
        )

    elif format_type == "json":
        return send_file(
            json_path, as_attachment=True, download_name=f"subtitles_{file_id}.json"
        )

    elif format_type == "fcpxml":
        content = generate_simple_fcpxml(subtitles, file_id)
        output_path = os.path.join(OUTPUT_FOLDER, f"{file_id}.fcpxml")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return send_file(
            output_path, as_attachment=True, download_name=f"timeline_{file_id}.fcpxml"
        )

    else:
        return jsonify({"error": "Unsupported format"}), 400


def format_srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}"


def generate_simple_fcpxml(subtitles: List[Dict], file_id: str) -> str:
    """生成简单 FCPXML"""
    if not subtitles:
        return ""

    total_duration = int(subtitles[-1]["end"]) if subtitles else 0

    root = ET.Element("fcpxml", version="1.9")

    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        {"id": "r0", "name": "1920x1080", "width": "1920", "height": "1080"},
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "Single Video"})

    sequence = ET.SubElement(
        event, "sequence", {"duration": f"{total_duration}s", "format": "r0"}
    )

    spine = ET.SubElement(sequence, "spine")

    for sub in subtitles:
        title = ET.SubElement(
            spine,
            "title",
            {
                "name": f"Subtitle {sub['id']}",
                "offset": f"{int(sub['start'])}s",
                "duration": f"{int(sub['end'] - sub['start'])}s",
            },
        )

        text = ET.SubElement(title, "text")
        text.text = sub["text"]

    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml_str
    return xml_str


# ==================== 多机位 API ====================


@app.route("/api/multi/projects", methods=["POST"])
def create_multi_project():
    """创建多机位项目"""
    data = request.json
    project_name = data.get(
        "name", f"Project_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    project_id = str(uuid.uuid4())[:8]
    multi_projects[project_id] = {
        "id": project_id,
        "name": project_name,
        "created_at": datetime.now().isoformat(),
        "cameras": [],
        "subtitles": [],
        "speakers": {},
    }

    return jsonify(
        {
            "success": True,
            "project_id": project_id,
            "project": multi_projects[project_id],
        }
    )


@app.route("/api/multi/projects/<project_id>/cameras", methods=["POST"])
def upload_multi_camera(project_id):
    """上传多机位视频"""
    if project_id not in multi_projects:
        return jsonify({"error": "Project not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    camera_name = request.form.get("name", file.filename)
    camera_index = int(request.form.get("index", 0))

    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file"}), 400

    file_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    video_path = os.path.join(UPLOAD_FOLDER, f"multi_{project_id}_{file_id}_{filename}")
    file.save(video_path)

    video_info = get_video_info(video_path)

    camera_data = {
        "id": file_id,
        "name": camera_name,
        "index": camera_index,
        "path": video_path,
        "filename": filename,
        **video_info,
        "status": "uploaded",
    }

    multi_projects[project_id]["cameras"].append(camera_data)

    return jsonify({"success": True, "camera": camera_data})


@app.route("/api/multi/projects/<project_id>/export", methods=["GET"])
def export_multi_project(project_id):
    """导出多机位项目"""
    format_type = request.args.get("format", "fcpxml")

    if project_id not in multi_projects:
        project_path = os.path.join(OUTPUT_FOLDER, f"multi_{project_id}_project.json")
        if os.path.exists(project_path):
            with open(project_path, "r", encoding="utf-8") as f:
                multi_projects[project_id] = json.load(f)
        else:
            return jsonify({"error": "Project not found"}), 404

    project = multi_projects[project_id]
    subtitles = project.get("subtitles", [])
    cameras = project.get("cameras", [])

    if format_type == "fcpxml":
        content = generate_multi_fcpxml(project)
        filename = f"{project['name']}.fcpxml"
    elif format_type == "premiere":
        content = generate_multi_premiere(project)
        filename = f"{project['name']}.xml"
    elif format_type == "srt":
        content = generate_multi_srt(subtitles, cameras)
        filename = f"{project['name']}_multicam.srt"
    elif format_type == "json":
        content = json.dumps(project, ensure_ascii=False, indent=2)
        filename = f"{project['name']}.json"
    else:
        return jsonify({"error": "Unsupported format"}), 400

    output_path = os.path.join(OUTPUT_FOLDER, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return send_file(output_path, as_attachment=True, download_name=filename)


def generate_multi_fcpxml(project):
    """生成多机位 FCPXML"""
    cameras = project.get("cameras", [])
    subtitles = project.get("subtitles", [])

    if not cameras:
        return ""

    max_duration = max(cam.get("duration", 0) for cam in cameras) if cameras else 0
    width = cameras[0].get("width", 1920)
    height = cameras[0].get("height", 1080)

    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")

    ET.SubElement(
        resources,
        "format",
        {
            "id": "r0",
            "name": f"{width}x{height}",
            "width": str(width),
            "height": str(height),
        },
    )

    # 多机位资源
    for i, cam in enumerate(cameras):
        ET.SubElement(
            resources,
            "asset",
            {
                "id": f"r{i + 1}",
                "name": cam.get("name", f"Camera {i + 1}"),
                "duration": f"{int(cam.get('duration', 0))}s",
            },
        )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": project.get("name", "Event")})

    sequence = ET.SubElement(
        event, "sequence", {"duration": f"{int(max_duration)}s", "format": "r0"}
    )
    spine = ET.SubElement(sequence, "spine")

    # 字幕
    for sub in subtitles:
        title = ET.SubElement(
            spine,
            "title",
            {
                "name": f"Subtitle {sub['id']}",
                "offset": f"{int(sub['start'])}s",
                "duration": f"{int(sub['end'] - sub['start'])}s",
            },
        )
        text = ET.SubElement(title, "text")
        text.text = f"[{sub.get('camera_name', 'Unknown')}] {sub['text']}"

    xml_str = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml_str


def generate_multi_premiere(project):
    """生成 Premiere XML"""
    cameras = project.get("cameras", [])
    subtitles = project.get("subtitles", [])
    max_duration = max(cam.get("duration", 0) for cam in cameras) if cameras else 0

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<PremiereData Version="3">',
        "  <Project>",
        f"    <Name>{project.get('name', 'Project')}</Name>",
        '    <Sequence ID="0">',
        f"      <Duration>{int(max_duration * 25)}</Duration>",
        "    </Sequence>",
        "    <Tracks>",
    ]

    for i, cam in enumerate(cameras):
        lines.extend(
            [
                f'      <Track Type="Video" Index="{i}">',
                f'        <ClipItem ID="{i}">',
                f"          <Name>{cam.get('name', f'Camera {i + 1}')}</Name>",
                "        </ClipItem>",
                "      </Track>",
            ]
        )

    lines.append("    </Tracks>")
    lines.append("  </Project>")
    lines.append("</PremiereData>")

    return "\n".join(lines)


def generate_multi_srt(subtitles, cameras):
    """生成多轨 SRT"""
    lines = []
    for i, sub in enumerate(subtitles, 1):
        camera = sub.get("camera_name", "Unknown")
        start = format_srt_time(sub["start"])
        end = format_srt_time(sub["end"])
        lines.extend(
            [
                str(i),
                f"{start} --> {end}",
                f"[{camera}] {sub.get('speaker', 'Unknown')}: {sub['text']}",
                "",
            ]
        )
    return "\n".join(lines)


@app.route("/api/multi/projects/<project_id>/process", methods=["POST"])
def process_multi_project(project_id):
    """处理多机位项目（异步后台线程）"""
    if project_id not in multi_projects:
        return jsonify({"error": "Project not found"}), 404

    project = multi_projects[project_id]
    cameras = project.get("cameras", [])

    if not cameras:
        return jsonify({"error": "No cameras in project"}), 400

    if not WHISPER_AVAILABLE:
        return jsonify({"error": "Whisper not available"}), 500

    # 初始化进度
    transcribe_progress[project_id] = {"percent": 0, "status": "running", "error": None}

    def _do_transcribe():
        try:
            all_subtitles = []
            total_cameras = len(cameras)

            for i, camera in enumerate(cameras):
                print(f"[INFO] Processing camera {i + 1}/{total_cameras}: {camera['name']}")
                camera["status"] = "processing"

                # 提取音频
                audio_path = os.path.join(
                    OUTPUT_FOLDER, f"{project_id}_{camera['id']}_audio.wav"
                )
                if not extract_audio(camera["path"], audio_path):
                    camera["status"] = "error"
                    continue

                # 进度回调（按机位分配进度）
                def progress_callback(p):
                    base = int((i / total_cameras) * 100)
                    chunk = int((p / 100) * (100 / total_cameras))
                    transcribe_progress[project_id]["percent"] = min(base + chunk, 99)

                # 转录
                subtitles = transcribe_with_whisper(
                    audio_path, "base", project_id, progress_callback
                )

                if not subtitles:
                    camera["status"] = "error"
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                    continue

                # 说话人分离
                speaker_segments = perform_speaker_diarization(audio_path, 2)
                subtitles = assign_speakers_to_subtitles(subtitles, speaker_segments)

                # 标记机位
                speaker_id = f"SPEAKER_{i:02d}"
                for sub in subtitles:
                    sub["camera_id"] = camera["id"]
                    sub["camera_name"] = camera["name"]
                    sub["speaker"] = speaker_id

                all_subtitles.extend(subtitles)
                camera["status"] = "completed"

                # 清理
                if os.path.exists(audio_path):
                    os.remove(audio_path)

            # 排序并重新编号
            all_subtitles.sort(key=lambda x: x["start"])
            for idx, sub in enumerate(all_subtitles):
                sub["id"] = idx

            project["subtitles"] = all_subtitles
            subtitle_storage[project_id] = all_subtitles

            # 统计说话人
            speakers = {}
            for sub in all_subtitles:
                spk = sub.get("speaker", "SPEAKER_00")
                if spk not in speakers:
                    speakers[spk] = {"count": 0, "camera": sub.get("camera_name", "Unknown")}
                speakers[spk]["count"] += 1
            project["speakers"] = speakers

            # 保存
            project_path = os.path.join(OUTPUT_FOLDER, f"multi_{project_id}_project.json")
            with open(project_path, "w", encoding="utf-8") as f:
                json.dump(project, f, ensure_ascii=False, indent=2)

            transcribe_progress[project_id] = {
                "percent": 100,
                "status": "done",
                "error": None,
            }
        except Exception as e:
            transcribe_progress[project_id] = {
                "percent": 0,
                "status": "error",
                "error": str(e),
            }

    # 启动后台线程
    thread = threading.Thread(target=_do_transcribe, daemon=True)
    thread.start()

    return jsonify({"status": "started", "project_id": project_id})


@app.route("/api/transcribe-progress", methods=["POST"])
def get_transcribe_progress():
    """查询转录进度"""
    data = request.json
    project_id = data.get("project_id", "")
    progress = transcribe_progress.get(
        project_id, {"percent": 0, "status": "unknown", "error": None}
    )
    return jsonify(progress)


# 添加兼容路由
@app.route("/api/multicam/bulk-match", methods=["POST", "OPTIONS"])
def multicam_bulk_match_compat():
    """兼容路由：/api/multicam/bulk-match"""
    return bulk_match()


@app.route("/api/bulk-match", methods=["POST", "OPTIONS"])
def bulk_match():
    """批量匹配大段文字到视频片段，支持多候选"""
    if request.method == "OPTIONS":
        return "", 200

    try:
        data = request.json
        project_id = data.get("project_id", "")
        text = data.get("text", "")

        if not project_id or not text:
            return jsonify({"error": "Missing project_id or text"}), 400

        # 获取字幕数据
        subtitles = subtitle_storage.get(project_id, [])
        if not subtitles:
            # 尝试从文件加载
            json_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_subtitles.json")
            multi_path = os.path.join(OUTPUT_FOLDER, f"multi_{project_id}_project.json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    subtitles = json.load(f)
            elif os.path.exists(multi_path):
                with open(multi_path, "r", encoding="utf-8") as f:
                    project_data = json.load(f)
                    subtitles = project_data.get("subtitles", [])
            else:
                return jsonify({"error": "Subtitles not found for this project"}), 404
            subtitle_storage[project_id] = subtitles

        # 拆分成句子（支持逗号、句号、感叹号、问号、换行分隔）
        sentences = re.split(r"[，。！？,!?\n]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]

        matches = []
        for sentence in sentences:
            if not sentence:
                continue

            # 搜索所有字幕，找出所有匹配的候选
            candidates = []
            for seg in subtitles:
                seg_text = seg.get("text", "")
                confidence = difflib.SequenceMatcher(None, sentence, seg_text).ratio()
                if confidence >= 0.3:
                    candidates.append(
                        {
                            "start": seg["start"],
                            "end": seg["end"],
                            "confidence": round(confidence, 3),
                            "subtitle_text": seg_text,
                        }
                    )

            # 按置信度排序，取前5个
            candidates.sort(key=lambda x: -x["confidence"])
            candidates = candidates[:5]

            # 过滤：只保留置信度 >= 0.6 的
            filtered = [c for c in candidates if c["confidence"] >= 0.6]

            matches.append(
                {
                    "sentence": sentence,
                    "candidates": filtered if filtered else candidates[:1],
                    "unmatched": len(filtered) == 0,
                }
            )

        return jsonify(
            {
                "success": True,
                "total": len(matches),
                "matched": sum(1 for m in matches if not m["unmatched"]),
                "matches": matches,
            }
        )
    except Exception as e:
        import traceback

        print(f"[ERROR] bulk_match exception: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ==================== 静态文件服务 ====================


@app.route("/", methods=["GET"])
def serve_index():
    """服务主页面"""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>", methods=["GET"])
def serve_static(path):
    """服务静态文件"""
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.exists(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "Not found"}), 404


# ==================== 兼容路由（对应前端 /multicam/xxx 调用） ====================

@app.route("/api/multicam/upload", methods=["POST"])
def multicam_upload_compat():
    """兼容：短视频上传"""
    return upload_single()


@app.route("/api/multicam/transcribe", methods=["POST"])
def multicam_transcribe_compat():
    """兼容：短视频转录"""
    data = request.json
    video_path = data.get("video_path")
    file_id = data.get("project_id") or data.get("file_id")

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 404

    if not file_id:
        file_id = str(uuid.uuid4())[:8]

    # 初始化进度
    transcribe_progress[file_id] = {"percent": 0, "status": "running", "error": None}

    # 确保项目存在
    if file_id not in single_projects:
        single_projects[file_id] = {
            "id": file_id,
            "video_path": video_path,
            "filename": os.path.basename(video_path),
        }

    def _do_transcribe():
        try:
            # 提取音频
            audio_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_audio.wav")
            if not extract_audio(video_path, audio_path):
                transcribe_progress[file_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Audio extraction failed",
                }
                return

            # 进度回调
            def progress_callback(p):
                transcribe_progress[file_id]["percent"] = p

            # 转录
            subtitles = transcribe_with_whisper(
                audio_path, "base", file_id, progress_callback
            )

            if not subtitles:
                transcribe_progress[file_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Transcription failed",
                }
                return

            # 说话人分离
            speaker_segments = perform_speaker_diarization(audio_path, 2)
            subtitles = assign_speakers_to_subtitles(subtitles, speaker_segments)

            # 保存到文件
            json_path = os.path.join(OUTPUT_FOLDER, f"{file_id}_subtitles.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(subtitles, f, ensure_ascii=False, indent=2)

            # 存储到内存
            single_projects[file_id]["subtitles"] = subtitles
            subtitle_storage[file_id] = subtitles

            # 清理
            if os.path.exists(audio_path):
                os.remove(audio_path)

            transcribe_progress[file_id] = {
                "percent": 100,
                "status": "done",
                "error": None,
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            transcribe_progress[file_id] = {
                "percent": 0,
                "status": "error",
                "error": str(e),
            }

    # 启动后台线程
    thread = threading.Thread(target=_do_transcribe, daemon=True)
    thread.start()

    return jsonify({"status": "started", "project_id": file_id})


@app.route("/api/multicam/transcribe-progress", methods=["POST"])
def multicam_progress_compat():
    """兼容：查询转录进度"""
    return get_transcribe_progress()


@app.route("/api/multicam/search", methods=["POST"])
def multicam_search_compat():
    """兼容：搜索台词"""
    try:
        data = request.json
        quotes = data.get("quotes", [])
        subtitles = data.get("subtitles", [])

        if not quotes or not subtitles:
            return jsonify({"error": "Missing quotes or subtitles"}), 400

        results = []
        for i, quote in enumerate(quotes):
            # 使用简单的文本匹配
            best_match = None
            best_score = 0

            for sub in subtitles:
                score = calculate_similarity(quote, sub.get("text", ""))
                if score > best_score:
                    best_score = score
                    best_match = sub

            if best_match and best_score > 60:
                results.append({
                    "index": i + 1,
                    "quote": quote,
                    "found": True,
                    "start": best_match["start"],
                    "end": best_match["end"],
                    "strategy": "exact",
                    "confidence": best_score / 100
                })
            else:
                results.append({
                    "index": i + 1,
                    "quote": quote,
                    "found": False,
                    "start": None,
                    "end": None,
                    "strategy": None,
                    "confidence": 0
                })

        return jsonify({
            "success": True,
            "total": len(quotes),
            "found": sum(1 for r in results if r["found"]),
            "results": results
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/concat", methods=["POST"])
def multicam_concat_compat():
    """兼容：拼接视频"""
    try:
        data = request.json
        video_paths = data.get("video_paths", [])
        segment_times = data.get("segment_times", [])
        project_id = data.get("project_id", "unknown")

        if not video_paths:
            return jsonify({"error": "No video paths"}), 400

        # 调用 FFmpeg 拼接 - 根据 segment_times 截取片段
        output_path = os.path.join(OUTPUT_FOLDER, f"concat_{project_id}.mp4")

        if segment_times and len(segment_times) > 0:
            # 先截取各个片段，再拼接
            segment_files = []
            video_path = video_paths[0]  # 使用第一个视频
            
            for i, (start, end) in enumerate(segment_times):
                segment_file = os.path.join(OUTPUT_FOLDER, f"segment_{project_id}_{i}.mp4")
                duration = end - start
                
                # 截取片段: -ss 开始时间 -t 持续时间
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-t", str(duration),
                    "-i", video_path,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    segment_file
                ]
                
                subprocess.run(cmd, check=True, capture_output=True)
                segment_files.append(segment_file)
            
            # 创建拼接列表
            list_file = os.path.join(OUTPUT_FOLDER, f"concat_list_{project_id}.txt")
            with open(list_file, "w", encoding="utf-8") as f:
                for seg_file in segment_files:
                    f.write(f"file '{seg_file}'\n")
            
            # 拼接所有片段
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                output_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            
            # 清理临时文件
            if os.path.exists(list_file):
                os.remove(list_file)
            for seg_file in segment_files:
                if os.path.exists(seg_file):
                    os.remove(seg_file)
        else:
            # 没有时间段，直接复制原视频
            video_path = video_paths[0]
            import shutil
            shutil.copy2(video_path, output_path)

        return jsonify({
            "success": True,
            "output_path": output_path
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/export-fcpxml", methods=["POST"])
def multicam_export_fcpxml_compat():
    """兼容：导出 FCPXML"""
    try:
        data = request.json
        video_paths = data.get("video_paths", [])
        segment_times = data.get("segment_times", [])
        project_name = data.get("project_name", "project")

        # 简化处理：直接调用单视频的导出逻辑
        return export_single(project_name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/export-prproj", methods=["POST"])
def multicam_export_prproj_compat():
    """兼容：导出 Premiere XML"""
    return multicam_export_fcpxml_compat()


@app.route("/api/multicam/export-edl", methods=["POST"])
def multicam_export_edl_compat():
    """兼容：导出 EDL"""
    try:
        data = request.json
        video_paths = data.get("video_paths", [])
        segment_times = data.get("segment_times", [])
        project_name = data.get("project_name", "project")

        output_path = os.path.join(OUTPUT_FOLDER, f"{project_name}.edl")

        # 生成简化版 EDL
        lines = ["TITLE: " + project_name, "FCM: NON-DROP FRAME", ""]

        for i, (start, end) in enumerate(segment_times, 1):
            lines.append(f"{i:03d}  AX       V     C        {format_edl_time(start)} {format_edl_time(end)} {format_edl_time(start)} {format_edl_time(end)}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return jsonify({
            "success": True,
            "edl_path": output_path
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/subtitles", methods=["GET"])
def multicam_subtitles_compat():
    """兼容：获取字幕"""
    project_id = request.args.get("project_id", "")

    # 尝试从单视频项目中获取
    if project_id in single_projects:
        return jsonify({
            "success": True,
            "subtitles": single_projects[project_id].get("subtitles", [])
        })

    # 尝试从文件加载
    json_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_subtitles.json")
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            subtitles = json.load(f)
        return jsonify({"success": True, "subtitles": subtitles})

    return jsonify({"error": "Subtitles not found"}), 404


def format_edl_time(seconds):
    """格式化 EDL 时间"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    frames = int((seconds % 1) * 25)  # 假设 25fps
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


if __name__ == "__main__":
    print("=" * 60)
    print("Video Subtitle Processor - Unified Edition")
    print("=" * 60)
    print(f"Whisper: {'Available' if WHISPER_AVAILABLE else 'Not Available'}")
    print(
        f"Speaker Diarization: {'Available' if DIARIZATION_AVAILABLE else 'Not Available'}"
    )
    print(f"Cache: {CACHE_DIR}")
    print("URL: http://localhost:5000")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5000, debug=True)
