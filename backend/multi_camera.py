"""
Video Subtitle Processor - 多机位访谈视频处理后端API (V2)
轻量级版本：内置简单说话人分离，不依赖 PyAnnote
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
import tempfile
import shutil

# 尝试导入 Whisper
try:
    import whisper

    WHISPER_AVAILABLE = True
    print("[INFO] Whisper loaded successfully")
except ImportError:
    WHISPER_AVAILABLE = False
    print("[WARNING] Whisper not installed")

# 导入轻量级说话人分离
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

# Configuration - 使用上级目录的路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
CACHE_DIR = r"C:\Users\36961\.cache\whisper"  # 使用本地缓存

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Global model cache
whisper_model = None
diarization_pipeline = None

# Project storage
projects: Dict[str, Dict] = {}

# Global progress tracking: project_id -> {"percent": int, "status": str, "error": str|None}
transcribe_progress = {}

# Global subtitle storage: project_id -> List[Dict]
subtitle_storage = {}

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "mxf"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_video_duration(video_path: str) -> float:
    """获取视频时长"""
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
    except Exception as e:
        print(f"[ERROR] Failed to get duration: {e}")
        return 0


def get_video_info(video_path: str) -> Dict:
    """获取视频详细信息"""
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
            "fps": eval(
                video_info.get("streams", [{}])[0].get("r_frame_rate", "30000/1001")
            ),
        }
    except Exception as e:
        print(f"[ERROR] Failed to get video info: {e}")
        return {
            "width": 1920,
            "height": 1080,
            "duration": 0,
            "bitrate": 0,
            "codec": "h264",
            "fps": 29.97,
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
    """使用 Whisper 转录音频，支持进度回调"""
    global whisper_model

    if not WHISPER_AVAILABLE:
        return []

    try:
        if whisper_model is None:
            print(f"[INFO] Loading Whisper model: {model_size}")
            # 使用本地缓存目录
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
    """执行说话人分离"""
    global diarization_pipeline

    if not DIARIZATION_AVAILABLE:
        return []

    try:
        if diarization_pipeline is None:
            print(f"[INFO] Initializing speaker diarization (speakers={num_speakers})")
            diarization_pipeline = create_pipeline(num_speakers)

        segments = diarization_pipeline(audio_path, num_speakers)
        print(f"[INFO] Found {len(segments)} speaker segments")
        return segments
    except Exception as e:
        print(f"[ERROR] Diarization failed: {e}")
        return []


def process_single_camera(
    camera: Dict,
    project_id: str,
    camera_index: int,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> List[Dict]:
    """处理单个机位，支持进度回调"""
    video_path = camera["path"]
    camera_id = camera["id"]

    print(f"[INFO] Processing camera {camera_index + 1}: {camera['name']}")

    # 提取音频
    audio_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_{camera_id}_audio.wav")
    if not extract_audio(video_path, audio_path):
        return []

    # 转录
    subtitles = transcribe_with_whisper(
        audio_path, "base", project_id, progress_callback
    )

    if not subtitles:
        # 清理
        if os.path.exists(audio_path):
            os.remove(audio_path)
        return []

    # 说话人分离
    speaker_segments = perform_speaker_diarization(audio_path, num_speakers=2)

    # 合并说话人信息到字幕
    subtitles = assign_speakers_to_subtitles(subtitles, speaker_segments)

    # 标记机位信息
    for sub in subtitles:
        sub["camera_id"] = camera_id
        sub["camera_name"] = camera["name"]
        sub["camera_index"] = camera_index

    # 清理音频文件
    if os.path.exists(audio_path):
        os.remove(audio_path)

    return subtitles


# ==================== XML 导出功能 ====================


def generate_multicamera_fcpxml(project: Dict) -> str:
    """生成多机位 FCPXML"""
    cameras = project.get("cameras", [])
    all_subtitles = project.get("subtitles", [])

    if not cameras:
        return ""

    max_duration = max(cam.get("duration", 0) for cam in cameras) if cameras else 0
    width = cameras[0].get("width", 1920)
    height = cameras[0].get("height", 1080)

    root = ET.Element("fcpxml", version="1.9")

    # Resources
    resources = ET.SubElement(root, "resources")

    format_el = ET.SubElement(
        resources,
        "format",
        {
            "id": "r0",
            "name": f"{width}x{height}",
            "width": str(width),
            "height": str(height),
        },
    )

    # 机位资源
    for i, cam in enumerate(cameras):
        asset_id = f"r{i + 1}"
        duration_frames = int(cam.get("duration", 0) * 30)

        asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": asset_id,
                "name": cam.get("name", f"Camera {i + 1}"),
                "uid": str(uuid.uuid4()),
                "start": "0s",
                "duration": f"{duration_frames}/30s",
                "hasVideo": "1",
                "hasAudio": "1",
            },
        )

        ET.SubElement(
            asset,
            "media-rep",
            {"kind": "original-media", "src": f"file://{cam.get('path', '')}"},
        )

    # 多机位资源
    mc_id = f"r{len(cameras) + 1}"
    mc_asset = ET.SubElement(
        resources,
        "mc-asset",
        {"id": mc_id, "name": f"{project.get('name', 'MultiCamera')} Multicam"},
    )

    for i, cam in enumerate(cameras):
        angle_id = f"a{i}"
        asset_id = f"r{i + 1}"
        duration_frames = int(cam.get("duration", 0) * 30)

        angle = ET.SubElement(
            mc_asset,
            "media",
            {
                "id": angle_id,
                "name": cam.get("name", f"Camera {i + 1}"),
                "angle": str(i),
                "duration": f"{duration_frames}/30s",
            },
        )

        ET.SubElement(angle, "video", {"ref": asset_id})
        ET.SubElement(angle, "audio", {"ref": asset_id})

    # Library
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": project.get("name", "Event")})

    # Sequence
    seq_duration_frames = int(max_duration * 30)
    sequence = ET.SubElement(
        event,
        "sequence",
        {"duration": f"{seq_duration_frames}/30s", "format": "r0", "tcStart": "0s"},
    )

    spine = ET.SubElement(sequence, "spine")

    # 多机位片段
    mc_clip = ET.SubElement(
        spine,
        "mc-clip",
        {
            "name": project.get("name", "Multicamera"),
            "ref": mc_id,
            "duration": f"{seq_duration_frames}/30s",
            "start": "0s",
        },
    )

    ET.SubElement(mc_clip, "mc-source", {"angleID": "a0", "srcEnable": "video"})

    # 字幕轨道
    if all_subtitles:
        # 按说话人分组创建字幕轨道
        speakers = list(set(sub.get("speaker", "SPEAKER_00") for sub in all_subtitles))

        for speaker_idx, speaker in enumerate(speakers):
            speaker_subs = [s for s in all_subtitles if s.get("speaker") == speaker]

            for sub in speaker_subs:
                start_frame = int(sub["start"] * 30)
                end_frame = int(sub["end"] * 30)
                duration = end_frame - start_frame

                title = ET.SubElement(
                    spine,
                    "title",
                    {
                        "name": f"{speaker}: {sub['text'][:20]}...",
                        "start": f"{start_frame}/30s",
                        "duration": f"{duration}/30s",
                        "lane": str(speaker_idx + 1),
                    },
                )

                # 文本样式
                text_style = ET.SubElement(
                    title, "text-style-def", {"id": f"ts{sub['id']}"}
                )
                text_style_el = ET.SubElement(
                    text_style,
                    "text-style",
                    {
                        "font": "PingFang SC",
                        "fontSize": "48",
                        "fontColor": "1 1 1 1",
                        "bold": "1",
                        "alignment": "center",
                    },
                )

                text = ET.SubElement(title, "text")
                text.text = f"[{speaker}] {sub['text']}"

    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + xml_str

    return xml_str


def generate_premiere_xml(project: Dict) -> str:
    """生成 Adobe Premiere XML"""
    cameras = project.get("cameras", [])
    all_subtitles = project.get("subtitles", [])

    if not cameras:
        return ""

    max_duration = max(cam.get("duration", 0) for cam in cameras) if cameras else 0
    width = cameras[0].get("width", 1920)
    height = cameras[0].get("height", 1080)

    # XMEML 格式
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<PremiereData Version="3">',
        "  <Project>",
        f"    <Name>{project.get('name', 'Project')}</Name>",
        '    <Sequence ID="0">',
        f"      <Name>{project.get('name', 'Sequence')}</Name>",
        f"      <Duration>{int(max_duration * 25)}</Duration>",
        "      <Settings>",
        "        <Video>",
        f"          <Width>{width}</Width>",
        f"          <Height>{height}</Height>",
        "          <FrameRate>25</FrameRate>",
        "        </Video>",
        "      </Settings>",
        "      <Tracks>",
    ]

    # 视频轨道（机位）
    for i, cam in enumerate(cameras):
        lines.extend(
            [
                f'        <Track Type="Video" Index="{i}">',
                f'          <ClipItem ID="{i}">',
                f"            <Name>{cam.get('name', f'Camera {i + 1}')}</Name>",
                "            <Start>0</Start>",
                f"            <End>{int(cam.get('duration', 0) * 25)}</End>",
                "            <In>0</In>",
                f"            <Out>{int(cam.get('duration', 0) * 25)}</Out>",
                f'            <File ID="{i}">',
                f"              <Name>{cam.get('filename', 'video.mp4')}</Name>",
                f"              <Path>{cam.get('path', '')}</Path>",
                "            </File>",
                "          </ClipItem>",
                "        </Track>",
            ]
        )

    # 字幕轨道
    if all_subtitles:
        lines.append(f'        <Track Type="Video" Index="{len(cameras)}">')
        for sub in all_subtitles:
            lines.extend(
                [
                    f'          <ClipItem ID="sub_{sub["id"]}">',
                    f"            <Name>Subtitle {sub['id']}</Name>",
                    f"            <Start>{int(sub['start'] * 25)}</Start>",
                    f"            <End>{int(sub['end'] * 25)}</End>",
                    "            <Type>Graphic</Type>",
                    f"            <Text>[{sub.get('speaker', 'Unknown')}] {sub['text']}</Text>",
                    "          </ClipItem>",
                ]
            )
        lines.append("        </Track>")

    lines.extend(
        [
            "      </Tracks>",
            "    </Sequence>",
            "  </Project>",
            "</PremiereData>",
        ]
    )

    return "\n".join(lines)


def generate_multitrack_srt(subtitles: List[Dict]) -> str:
    """生成多轨 SRT"""
    lines = []

    for i, sub in enumerate(subtitles, 1):
        speaker = sub.get("speaker", "SPEAKER_00")
        camera = sub.get("camera_name", "Unknown")

        start = format_srt_time(sub["start"])
        end = format_srt_time(sub["end"])
        text = f"[{camera}] {speaker}: {sub['text']}"

        lines.extend([str(i), f"{start} --> {end}", text, ""])

    return "\n".join(lines)


def format_srt_time(seconds: float) -> str:
    """格式化 SRT 时间"""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}"


# ==================== API Routes ====================


@app.route("/api/health", methods=["GET"])
def health_check():
    """健康检查"""
    return jsonify(
        {
            "status": "healthy",
            "whisper_available": WHISPER_AVAILABLE,
            "diarization_available": DIARIZATION_AVAILABLE,
            "cache_dir": CACHE_DIR,
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/projects", methods=["POST"])
def create_project():
    """创建新项目"""
    data = request.json
    project_name = data.get(
        "name", f"Project_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    project_id = str(uuid.uuid4())[:8]
    projects[project_id] = {
        "id": project_id,
        "name": project_name,
        "created_at": datetime.now().isoformat(),
        "cameras": [],
        "subtitles": [],
        "speakers": {},
    }

    return jsonify(
        {"success": True, "project_id": project_id, "project": projects[project_id]}
    )


@app.route("/api/projects/<project_id>/cameras", methods=["POST"])
def upload_camera(project_id):
    """上传机位视频"""
    if project_id not in projects:
        return jsonify({"error": "Project not found"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    camera_name = request.form.get("name", file.filename)
    camera_index = int(request.form.get("index", 0))

    if file.filename == "" or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file"}), 400

    # 保存文件
    file_id = str(uuid.uuid4())[:8]
    filename = secure_filename(file.filename)
    video_path = os.path.join(UPLOAD_FOLDER, f"{project_id}_{file_id}_{filename}")
    file.save(video_path)

    # 获取视频信息
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

    projects[project_id]["cameras"].append(camera_data)

    return jsonify({"success": True, "camera": camera_data})


@app.route("/api/projects/<project_id>/process", methods=["POST"])
def process_project(project_id):
    """处理项目（异步后台线程）"""
    if project_id not in projects:
        return jsonify({"error": "Project not found"}), 404

    project = projects[project_id]
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
                # 进度回调（按机位分配进度）
                def progress_callback(p):
                    base = int((i / total_cameras) * 100)
                    chunk = int((p / 100) * (100 / total_cameras))
                    transcribe_progress[project_id]["percent"] = min(base + chunk, 99)

                subtitles = process_single_camera(camera, project_id, i, progress_callback)
                all_subtitles.extend(subtitles)

                # 更新状态
                camera["status"] = "completed" if subtitles else "error"

            # 按时间排序
            all_subtitles.sort(key=lambda x: x["start"])

            # 重新分配ID
            for idx, sub in enumerate(all_subtitles):
                sub["id"] = idx

            project["subtitles"] = all_subtitles
            subtitle_storage[project_id] = all_subtitles

            # 统计说话人
            speakers = {}
            for sub in all_subtitles:
                spk = sub.get("speaker", "SPEAKER_00")
                cam = sub.get("camera_name", "Unknown")
                if spk not in speakers:
                    speakers[spk] = {"count": 0, "camera": cam}
                speakers[spk]["count"] += 1
            project["speakers"] = speakers

            # 保存项目
            project_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_project.json")
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
            project_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_project.json")
            if os.path.exists(project_path):
                with open(project_path, "r", encoding="utf-8") as f:
                    project_data = json.load(f)
                    subtitles = project_data.get("subtitles", [])
            else:
                return jsonify({"error": "Subtitles not found for this project"}), 404
            subtitle_storage[project_id] = subtitles

        # 拆分成句子
        sentences = re.split(r"[。！？.!?\n]+", text)
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


@app.route("/api/projects/<project_id>", methods=["GET"])
def get_project(project_id):
    """获取项目详情"""
    if project_id not in projects:
        project_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_project.json")
        if os.path.exists(project_path):
            with open(project_path, "r", encoding="utf-8") as f:
                projects[project_id] = json.load(f)
        else:
            return jsonify({"error": "Project not found"}), 404

    return jsonify({"success": True, "project": projects[project_id]})


@app.route("/api/projects/<project_id>/export", methods=["GET"])
def export_project(project_id):
    """导出项目"""
    format_type = request.args.get("format", "fcpxml")

    if project_id not in projects:
        project_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_project.json")
        if os.path.exists(project_path):
            with open(project_path, "r", encoding="utf-8") as f:
                projects[project_id] = json.load(f)
        else:
            return jsonify({"error": "Project not found"}), 404

    project = projects[project_id]

    if format_type == "fcpxml":
        content = generate_multicamera_fcpxml(project)
        filename = f"{project['name']}.fcpxml"
        mime_type = "text/xml"
    elif format_type == "premiere":
        content = generate_premiere_xml(project)
        filename = f"{project['name']}.xml"
        mime_type = "text/xml"
    elif format_type == "srt":
        content = generate_multitrack_srt(project.get("subtitles", []))
        filename = f"{project['name']}_multicam.srt"
        mime_type = "text/plain"
    elif format_type == "json":
        content = json.dumps(project, ensure_ascii=False, indent=2)
        filename = f"{project['name']}.json"
        mime_type = "application/json"
    else:
        return jsonify({"error": "Unsupported format"}), 400

    output_path = os.path.join(OUTPUT_FOLDER, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return send_file(
        output_path, as_attachment=True, download_name=filename, mimetype=mime_type
    )


@app.route("/api/projects/<project_id>/search", methods=["POST"])
def search_project_subtitles(project_id):
    """搜索字幕"""
    if project_id not in projects:
        return jsonify({"error": "Project not found"}), 404

    data = request.json
    keyword = data.get("keyword", "").lower()

    if not keyword:
        return jsonify({"error": "No keyword provided"}), 400

    project = projects[project_id]
    subtitles = project.get("subtitles", [])

    results = [sub for sub in subtitles if keyword in sub.get("text", "").lower()]

    return jsonify(
        {"success": True, "keyword": keyword, "results": results, "total": len(results)}
    )


@app.route("/api/projects", methods=["GET"])
def list_projects():
    """列出所有项目"""
    return jsonify(
        {
            "success": True,
            "projects": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "created_at": p["created_at"],
                    "cameras": len(p.get("cameras", [])),
                    "subtitles": len(p.get("subtitles", [])),
                }
                for p in projects.values()
            ],
        }
    )


@app.route("/api/projects/<project_id>", methods=["DELETE"])
def delete_project(project_id):
    """删除项目"""
    if project_id in projects:
        project = projects[project_id]

        # 删除关联文件
        for cam in project.get("cameras", []):
            if os.path.exists(cam.get("path", "")):
                os.remove(cam["path"])

        project_path = os.path.join(OUTPUT_FOLDER, f"{project_id}_project.json")
        if os.path.exists(project_path):
            os.remove(project_path)

        del projects[project_id]

    return jsonify({"success": True})


# Serve static files
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend", "multi")


@app.route("/", methods=["GET"])
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>", methods=["GET"])
def serve_static(path):
    """Serve static files"""
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.exists(file_path):
        return send_from_directory(FRONTEND_DIR, path)
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    print("=" * 60)
    print("Video Subtitle Processor - MultiCamera Edition V2")
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
