"""
多机位视频字幕拼接处理器
整合 Whisper + 模糊搜索 + FFmpeg多画面拼接 + FCPXML生成
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import uuid
import json
import subprocess
import re
import difflib
from datetime import datetime
from werkzeug.utils import secure_filename
from typing import List, Dict, Any, Tuple, Optional, Callable
import threading

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
FFMPEG_PATH = r"C:\Users\36961\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build-shared\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\Users\36961\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build-shared\bin\ffprobe.exe"
CACHE_DIR = r"C:\Users\36961\.cache\whisper"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "mxf"}

# Global model cache
whisper_model = None

# Global progress tracking: project_id -> {"percent": int, "status": str, "error": str|None}
transcribe_progress = {}

# Global subtitle storage: project_id -> List[Dict]
subtitle_storage = {}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ==================== 视频处理 ====================


def get_video_info(video_path: str) -> Dict:
    """获取视频信息"""
    try:
        cmd_video = [
            FFPROBE_PATH,
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
        result_video = subprocess.run(
            cmd_video,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        video_info = json.loads(result_video.stdout)

        cmd_format = [
            FFPROBE_PATH,
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate",
            "-of",
            "json",
            video_path,
        ]
        result_format = subprocess.run(
            cmd_format,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        format_info = json.loads(result_format.stdout)

        stream_info = video_info.get("streams", [{}])[0]
        fps_str = stream_info.get("r_frame_rate", "30/1")
        # 解析帧率分数，如 "30/1" -> 30
        if "/" in fps_str:
            num, den = fps_str.split("/")
            fps = int(num) / int(den)
        else:
            fps = float(fps_str)

        return {
            "width": stream_info.get("width", 1920),
            "height": stream_info.get("height", 1080),
            "duration": float(format_info.get("format", {}).get("duration", 0)),
            "codec": stream_info.get("codec_name", "h264"),
            "fps": fps,
        }
    except Exception as e:
        print(f"[ERROR] Failed to get video info: {e}")
        return {
            "width": 1920,
            "height": 1080,
            "duration": 0,
            "codec": "h264",
            "fps": 30,
        }


def extract_audio(video_path: str, audio_path: str) -> bool:
    """提取音频"""
    try:
        command = [
            FFMPEG_PATH,
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
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
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

    try:
        import whisper
    except ImportError:
        print("[ERROR] Whisper not installed")
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


# ==================== 模糊搜索 ====================


def extract_core_keywords(sentence: str) -> List[str]:
    """提取核心关键词（去停用词）"""
    stop_words = {
        "其实",
        "就是",
        "然后",
        "但是",
        "所以",
        "因为",
        "对",
        "嗯",
        "啊",
        "呢",
        "吧",
        "了",
        "的",
        "是",
        "在",
        "有",
        "我",
        "你",
        "他",
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "这样",
        "那样",
        "什么",
        "怎么",
        "一个",
        "一些",
    }
    # 移除非中文字符
    clean = re.sub(r"[^\u4e00-\u9fa5]", "", sentence)
    keywords = []
    for length in range(min(6, len(clean)), 2, -1):
        for i in range(len(clean) - length + 1):
            word = clean[i : i + length]
            if word not in stop_words:
                keywords.append(word)
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:8]


def split_into_short_phrases(sentence: str) -> List[str]:
    """分割为短词组"""
    parts = re.split(r"[，、]", sentence)
    phrases = []
    for part in parts:
        part = part.strip()
        if 4 <= len(part) <= 15:
            phrases.append(part)
    return phrases[:3]


def calculate_similarity(text1: str, text2: str) -> float:
    """计算文本相似度"""
    return difflib.SequenceMatcher(None, text1, text2).ratio() * 100


def search_keyword(keyword: str, subtitles: List[Dict]) -> List[Dict]:
    """在字幕中搜索关键词"""
    matches = []
    for seg in subtitles:
        text = seg.get("text", "")
        if keyword in text:
            similarity = calculate_similarity(keyword, text)
            matches.append(
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": text,
                    "similarity": similarity,
                }
            )
    matches.sort(key=lambda x: -x["similarity"])
    return matches


def fuzzy_search_sentence(sentence: str, subtitles: List[Dict]) -> Dict:
    """
    5级模糊搜索定位台词
    返回: {'found': bool, 'strategy': str, 'match': dict}
    """
    sentence = sentence.strip()
    if not sentence:
        return {"found": False, "strategy": None, "match": None}

    # 策略1：整句包含匹配（更宽松）
    for seg in subtitles:
        text = seg.get("text", "")
        if sentence in text:
            return {
                "found": True,
                "strategy": "整句包含",
                "match": {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": text,
                    "similarity": 100,
                },
            }

    # 策略2：反向包含 - 字幕在台词中
    for seg in subtitles:
        text = seg.get("text", "")
        if text in sentence:
            return {
                "found": True,
                "strategy": "字幕包含在台词中",
                "match": {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": text,
                    "similarity": 100,
                },
            }

    # 策略3：核心关键词
    keywords = extract_core_keywords(sentence)
    for kw in keywords[:5]:
        if len(kw) >= 2:
            for seg in subtitles:
                text = seg.get("text", "")
                if kw in text:
                    return {
                        "found": True,
                        "strategy": f"关键词: {kw}",
                        "match": {
                            "start": seg["start"],
                            "end": seg["end"],
                            "text": text,
                            "similarity": 80,
                        },
                    }

    # 策略4：短词组
    phrases = split_into_short_phrases(sentence)
    for phrase in phrases:
        if len(phrase) >= 3:
            for seg in subtitles:
                text = seg.get("text", "")
                if phrase in text:
                    return {
                        "found": True,
                        "strategy": f"词组: {phrase}",
                        "match": {
                            "start": seg["start"],
                            "end": seg["end"],
                            "text": text,
                            "similarity": 70,
                        },
                    }

    # 策略5：实词（2字以上）
    words = re.findall(r"[\u4e00-\u9fa5]{2,}", sentence)
    for word in words:
        if len(word) >= 2:
            for seg in subtitles:
                text = seg.get("text", "")
                if word in text:
                    return {
                        "found": True,
                        "strategy": f"实词: {word}",
                        "match": {
                            "start": seg["start"],
                            "end": seg["end"],
                            "text": text,
                            "similarity": 60,
                        },
                    }

    # 策略6：2字组合
    for i in range(0, len(sentence) - 1, 2):
        pair = sentence[i : i + 2]
        if re.match(r"[\u4e00-\u9fa5]{2}", pair):
            for seg in subtitles:
                text = seg.get("text", "")
                if pair in text:
                    return {
                        "found": True,
                        "strategy": f"二字: {pair}",
                        "match": {
                            "start": seg["start"],
                            "end": seg["end"],
                            "text": text,
                            "similarity": 50,
                        },
                    }

    # 兜底：找最相似的（降低阈值到15%）
    best_match = None
    best_sim = 0
    for seg in subtitles:
        sim = calculate_similarity(sentence, seg.get("text", ""))
        if sim > best_sim:
            best_sim = sim
            best_match = seg

    if best_match and best_sim > 15:
        return {
            "found": True,
            "strategy": f"相似度{int(best_sim)}%",
            "match": {
                "start": best_match["start"],
                "end": best_match["end"],
                "text": best_match["text"],
                "similarity": best_sim,
            },
        }

    return {"found": False, "strategy": None, "match": None}


def search_quotes_in_order(quotes: List[str], subtitles: List[Dict]) -> List[Dict]:
    """按顺序搜索多句台词，返回每个的时间点"""
    results = []
    for i, quote in enumerate(quotes):
        quote = quote.strip()
        if not quote:
            continue
        result = fuzzy_search_sentence(quote, subtitles)
        results.append(
            {
                "index": i + 1,
                "quote": quote,
                "found": result["found"],
                "strategy": result["strategy"],
                "start": result["match"]["start"] if result["match"] else None,
                "end": result["match"]["end"] if result["match"] else None,
                "matched_text": result["match"]["text"] if result["match"] else None,
                "similarity": result["match"].get("similarity", 100)
                if result["match"]
                else 0,
            }
        )
    return results


# ==================== FFmpeg 视频拼接 ====================


def cut_segment(video_path: str, start: float, end: float, output_path: str) -> bool:
    """裁剪视频片段（使用重新编码确保兼容性）"""
    try:
        duration = end - start
        command = [
            FFMPEG_PATH,
            "-y",
            "-ss",
            str(start),
            "-i",
            video_path,
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-avoid_negative_ts",
            "make_zero",
            output_path,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            print(f"[ERROR] Cut segment failed: {result.stderr}")
            return False
        return os.path.exists(output_path)
    except Exception as e:
        print(f"[ERROR] Cut segment failed: {e}")
        return False


def create_multiview(
    video_paths: List[str], segment_times: List[Tuple[float, float]], output_path: str
) -> bool:
    """
    创建多画面拼接视频（1x3横排）
    video_paths: [机位1路径, 机位2路径, 机位3路径]
    segment_times: [(start, end), ...] 每个片段的时间点（基于机位1）
    """
    try:
        print(
            f"[DEBUG] create_multiview called: video_paths={video_paths}, segment_times={segment_times}"
        )

        # 清理旧的临时文件
        import glob

        for f in glob.glob(os.path.join(OUTPUT_FOLDER, "temp_seg_*.mp4")):
            try:
                os.remove(f)
            except:
                pass
        for f in glob.glob(os.path.join(OUTPUT_FOLDER, "temp_mv_*.mp4")):
            try:
                os.remove(f)
            except:
                pass

        temp_segments = []

        # 1. 先裁剪每个机位在各个时间点的片段
        for seg_idx, (start, end) in enumerate(segment_times):
            print(
                f"[DEBUG] Cutting segment {seg_idx}: start={start}, end={end}, duration={end - start}"
            )
            seg_paths = []
            for cam_idx, video_path in enumerate(video_paths):
                seg_path = os.path.join(
                    OUTPUT_FOLDER, f"temp_seg_{seg_idx}_cam{cam_idx}.mp4"
                )
                print(f"[DEBUG]   Cam{cam_idx}: cutting {video_path} -> {seg_path}")
                if cut_segment(video_path, start, end, seg_path):
                    seg_paths.append(seg_path)
                    print(
                        f"[DEBUG]   Cam{cam_idx}: cut success, file exists={os.path.exists(seg_path)}"
                    )
                else:
                    print(f"[DEBUG]   Cam{cam_idx}: cut FAILED")
                    return False
            temp_segments.append(seg_paths)

        # 2. 如果只有一个片段，直接多画面拼接
        if len(temp_segments) == 1:
            seg_paths = temp_segments[0]
            if len(seg_paths) == 3:
                # 1x3横排拼接
                command = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    seg_paths[0],
                    "-i",
                    seg_paths[1],
                    "-i",
                    seg_paths[2],
                    "-filter_complex",
                    "[0:v][1:v][2:v]hstack=inputs=3[vout]",
                    "-map",
                    "[vout]",
                    "-map",
                    "0:a",  # 只用机位1音频
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    output_path,
                ]
            elif len(seg_paths) == 2:
                command = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    seg_paths[0],
                    "-i",
                    seg_paths[1],
                    "-filter_complex",
                    "[0:v][1:v]hstack=inputs=2[vout]",
                    "-map",
                    "[vout]",
                    "-map",
                    "0:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    output_path,
                ]
            else:
                # 单机位
                import shutil

                shutil.copy(seg_paths[0], output_path)
                return True

            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
            )
            return True

        # 3. 多个片段：先拼接各机位，再多画面合并，最后concat
        # 简化处理：每个片段单独生成多画面，再拼接

        multiview_segments = []
        for seg_idx, seg_paths in enumerate(temp_segments):
            mv_path = os.path.join(OUTPUT_FOLDER, f"temp_mv_{seg_idx}.mp4")

            if len(seg_paths) == 3:
                command = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    seg_paths[0],
                    "-i",
                    seg_paths[1],
                    "-i",
                    seg_paths[2],
                    "-filter_complex",
                    "[0:v][1:v][2:v]hstack=inputs=3[vout]",
                    "-map",
                    "[vout]",
                    "-map",
                    "0:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    mv_path,
                ]
                subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                )
            elif len(seg_paths) == 2:
                command = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    seg_paths[0],
                    "-i",
                    seg_paths[1],
                    "-filter_complex",
                    "[0:v][1:v]hstack=inputs=2[vout]",
                    "-map",
                    "[vout]",
                    "-map",
                    "0:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "23",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    mv_path,
                ]
                subprocess.run(
                    command,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    encoding="utf-8",
                    errors="replace",
                )
            else:
                import shutil

                shutil.copy(seg_paths[0], mv_path)
            multiview_segments.append(mv_path)

        # 4. 拼接所有多画面片段
        print(f"[DEBUG] Concatenating {len(multiview_segments)} multiview segments")
        concat_list = os.path.join(OUTPUT_FOLDER, "concat_list.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for mv_path in multiview_segments:
                print(f"[DEBUG] Adding to concat list: {mv_path}")
                f.write(f"file '{mv_path}'\n")

        print(f"[DEBUG] Running concat command with {len(multiview_segments)} segments")
        command = [
            FFMPEG_PATH,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list,
            "-c",
            "copy",
            output_path,
        ]
        print(f"[DEBUG] Concat command: {' '.join(command)}")
        result = subprocess.run(
            command, capture_output=True, encoding="utf-8", errors="replace"
        )
        print(f"[DEBUG] Concat result: returncode={result.returncode}")
        if result.returncode != 0:
            print(f"[DEBUG] Concat stderr: {result.stderr}")

        # 5. 验证输出文件
        if not os.path.exists(output_path):
            print(f"[ERROR] Output file not created: {output_path}")
            return False

        # 6. 清理临时文件
        for seg_path in multiview_segments:
            try:
                os.remove(seg_path)
            except:
                pass
        for seg_paths in temp_segments:
            for seg_path in seg_paths:
                try:
                    os.remove(seg_path)
                except:
                    pass

        return True

    except Exception as e:
        import traceback

        print(f"[ERROR] Create multiview failed: {e}")
        traceback.print_exc()
        return False


# ==================== FCPXML 生成 ====================


def generate_fcpxml(
    project_name: str,
    video_paths: List[str],
    segment_times: List[Tuple[float, float]],
    output_width: int = 1920,
    output_height: int = 1080,
    fps: int = 30,
    mac_video_path: str = None,
) -> str:
    """生成剪映兼容的FCPXML - 引用原始视频，每个片段独立asset，asset标签带start偏移"""

    total_duration = sum(end - start for start, end in segment_times)
    total_frames = int(total_duration * fps)
    frame_duration = f"1/{fps}s"

    import hashlib

    video_path = video_paths[0] if video_paths else ""
    filename = os.path.basename(video_path) if video_path else "video.mp4"
    if mac_video_path:
        src = "file://" + mac_video_path
    else:
        src = "file:///" + video_path.replace("\\", "/")

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<!DOCTYPE fcpxml>")
    lines.append('<fcpxml version="1.9">')
    lines.append("  <resources>")
    lines.append(
        f'    <format id="f1" width="{output_width}" height="{output_height}" frameDuration="{frame_duration}" colorSpace="BT.709"/>'
    )

    # 剪映兼容格式：每个片段一个独立asset，指向同一原始视频，但asset上带start偏移
    # 时间格式混合：start用帧格式，duration用小数秒（和旧版成功文件一致）
    for seg_idx, (start, end) in enumerate(segment_times):
        uid = (
            f"asset_{hashlib.md5((video_path + str(seg_idx)).encode()).hexdigest()[:8]}"
        )
        start_str = f"{round(start, 3)}s"
        lines.append(
            f'    <asset id="r{seg_idx + 2}" name="{filename}" uid="{uid}" src="{src}" start="{start_str}" hasVideo="1" hasAudio="1"/>'
        )

    lines.append("  </resources>")
    lines.append("  <library>")
    lines.append(f'    <event name="{project_name}">')
    lines.append(f'      <project name="{project_name}">')
    lines.append(f'        <sequence format="f1" duration="{total_frames}/{fps}s">')
    lines.append("          <spine>")

    current_frame = 0
    for seg_idx, (start, end) in enumerate(segment_times):
        duration = end - start
        duration_frames = int(duration * fps)

        duration_str = f"{round(duration, 3)}s"
        start_str = f"{round(start, 3)}s"
        offset_str = "0s" if current_frame == 0 else f"{current_frame}/{fps}s"

        lines.append(
            f'            <asset-clip ref="r{seg_idx + 2}" name="seg{seg_idx + 1}" offset="{offset_str}" duration="{duration_str}" start="{start_str}"/>'
        )

        current_frame += duration_frames

    lines.append("          </spine>")
    lines.append("        </sequence>")
    lines.append("      </project>")
    lines.append("    </event>")
    lines.append("  </library>")
    lines.append("</fcpxml>")

    return "\n".join(lines)


def generate_prproj(
    project_name: str,
    video_paths: List[str],
    segment_times: List[Tuple[float, float]],
    output_width: int = 1920,
    output_height: int = 1080,
    fps: int = 30,
) -> str:
    """生成Premiere Pro工程文件 (PRPROJ) - 使用更完整的XML格式"""
    import hashlib

    total_duration = sum(end - start for start, end in segment_times)
    project_uid = hashlib.md5(project_name.encode()).hexdigest()[:16]

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<PremiereData Version="4" Name="' + project_name + '">')
    lines.append('  <Project ObjectUID="' + project_uid + '">')
    lines.append('    <Children ArraySize="' + str(len(segment_times) + 1) + '">')

    # 添加视频文件引用 (Media)
    for i, video_path in enumerate(video_paths):
        filename = os.path.basename(video_path)
        media_uid = hashlib.md5(video_path.encode()).hexdigest()[:16]
        src = "file:///" + video_path.replace("\\", "/")
        lines.append(
            '      <Media ObjectUID="'
            + str(i + 2)
            + '" Name="'
            + filename
            + '" TrackIndex="1">'
        )
        lines.append('        <MediaParent ObjectUID="1"/>')
        lines.append('        <Source src="' + src + '" TrackIndex="1"/>')
        lines.append("        <FilePath>" + src + "</FilePath>")
        lines.append("      </Media>")

    # 添加序列
    seq_uid = hashlib.md5((project_name + "_sequence").encode()).hexdigest()[:16]
    lines.append(
        '      <Sequence ObjectUID="'
        + seq_uid
        + '" Name="'
        + project_name
        + '_Sequence">'
    )
    lines.append('        <Children ArraySize="1">')

    # 添加视频轨道
    track_uid = hashlib.md5((project_name + "_track").encode()).hexdigest()[:16]
    lines.append('          <Track ObjectUID="' + track_uid + '" TrackUID="0">')
    lines.append("            <TrackName>Video 1</TrackName>")
    lines.append("            <TrackHeight>50</TrackHeight>")
    lines.append("            <MIO><![CDATA[]]></MIO>")
    lines.append('            <Clips ArraySize="' + str(len(segment_times)) + '">')

    current_time = 0.0
    for seg_idx, (start, end) in enumerate(segment_times):
        duration = end - start
        start_sec = round(start, 3)
        end_sec = round(end, 3)
        current_sec = round(current_time, 3)

        clip_uid = hashlib.md5((f"{project_name}_clip_{seg_idx}").encode()).hexdigest()[
            :16
        ]
        lines.append(
            '              <Clip ObjectUID="'
            + str(seg_idx + 100)
            + '" ClipUID="'
            + clip_uid
            + '" Name="Clip_'
            + str(seg_idx + 1)
            + '">'
        )
        lines.append("                <Start>" + str(current_sec) + "</Start>")
        lines.append(
            "                <End>" + str(round(current_time + duration, 3)) + "</End>"
        )
        lines.append("                <InPoint>" + str(start_sec) + "</InPoint>")
        lines.append("                <OutPoint>" + str(end_sec) + "</OutPoint>")
        lines.append('                <File ObjectUID="2"/>')
        lines.append("                <FrameRect>")
        lines.append("                  <Top>0</Top>")
        lines.append("                  <Left>0</Left>")
        lines.append("                  <Right>" + str(output_width) + "</Right>")
        lines.append("                  <Bottom>" + str(output_height) + "</Bottom>")
        lines.append("                </FrameRect>")
        lines.append("              </Clip>")

        current_time += duration

    lines.append("            </Clips>")
    lines.append("          </Track>")
    lines.append("        </Children>")

    # 序列属性
    lines.append("        <FrameRect>")
    lines.append("          <Top>0</Top>")
    lines.append("          <Left>0</Left>")
    lines.append("          <Right>" + str(output_width) + "</Right>")
    lines.append("          <Bottom>" + str(output_height) + "</Bottom>")
    lines.append("        </FrameRect>")
    lines.append("        <FrameRate>" + str(int(fps)) + "</FrameRate>")
    lines.append("        <SampleRate>48000</SampleRate>")
    lines.append(
        "        <PreviewPreset>Seq_Format_"
        + str(output_width)
        + "x"
        + str(output_height)
        + "_"
        + str(int(fps))
        + "p</PreviewPreset>"
    )
    lines.append("        <PixelAspectRatio>1.0</PixelAspectRatio>")
    lines.append("        <FieldDominance>none</FieldDominance>")
    lines.append("        <ZeroPoint>0</ZeroPoint>")
    lines.append("      </Sequence>")

    lines.append("    </Children>")
    lines.append("  </Project>")
    lines.append("</PremiereData>")

    return "\n".join(lines)


def seconds_to_timecode(seconds: float, fps: int) -> str:
    """将秒数转换为 HH:MM:SS:FF 时间码"""
    total_frames = int(seconds * fps)
    hours = total_frames // (fps * 3600)
    minutes = (total_frames % (fps * 3600)) // (fps * 60)
    secs = (total_frames % (fps * 60)) // fps
    frames = total_frames % fps
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def generate_edl(
    project_name: str,
    video_paths: List[str],
    segment_times: List[Tuple[float, float]],
    fps: int = 30,
) -> str:
    """生成 Premiere Pro 兼容的 CMX3600 EDL 格式文件"""
    video_path = video_paths[0] if video_paths else ""
    filename = os.path.basename(video_path) if video_path else "video.mp4"
    src_file = "file:///" + video_path.replace("\\", "/")
    lines = []
    lines.append(f"TITLE: {project_name}")
    lines.append("FCM: NON-DROP FRAME")
    lines.append("")

    current_time = 0.0
    for seg_idx, (start, end) in enumerate(segment_times):
        event_num = str(seg_idx + 1).zfill(3)

        # 先计算帧数，确保 src 和 rec 时长严格一致
        src_in_frames = int(start * fps)
        src_out_frames = int(end * fps)
        rec_in_frames = int(current_time * fps)
        duration_frames = src_out_frames - src_in_frames
        rec_out_frames = rec_in_frames + duration_frames

        src_in = seconds_to_timecode(start, fps)
        src_out = seconds_to_timecode(end, fps)
        rec_in = seconds_to_timecode(current_time, fps)
        rec_out = seconds_to_timecode(rec_out_frames / fps, fps)

        # 视频轨
        lines.append(
            f"{event_num}  AX       V     C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {filename}")
        lines.append(f"* SOURCE FILE: {src_file}")
        # 音频轨（同一段素材的音频， Premiere Pro 会识别为配套音轨）
        lines.append(
            f"{event_num}  AX       A2    C        {src_in} {src_out} {rec_in} {rec_out}"
        )
        lines.append(f"* FROM CLIP NAME: {filename}")
        lines.append(f"* SOURCE FILE: {src_file}")
        lines.append("")

        current_time += duration_frames / fps

    return "\n".join(lines)


# ==================== Flask API ====================


@app.route("/api/health", methods=["GET", "OPTIONS"])
def health_check():
    """健康检查"""
    if request.method == "OPTIONS":
        return "", 200
    return jsonify(
        {
            "status": "healthy",
            "ffmpeg": os.path.exists(FFMPEG_PATH),
            "whisper": "whisper" in globals(),
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/api/multicam/upload", methods=["POST"])
def upload_multicam():
    """上传多机位视频"""
    files = request.files.getlist("videos")

    if len(files) < 1:
        return jsonify({"error": "需要至少1个视频"}), 400

    project_id = str(uuid.uuid4())[:8]
    video_paths = []

    for i, file in enumerate(files[:3]):  # 最多3个
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            video_path = os.path.join(
                UPLOAD_FOLDER, f"mc_{project_id}_cam{i + 1}_{filename}"
            )
            file.save(video_path)
            video_paths.append(video_path)

    video_info = get_video_info(video_paths[0])

    return jsonify(
        {
            "success": True,
            "project_id": project_id,
            "camera_count": len(video_paths),
            "video_paths": video_paths,
            "duration": video_info["duration"],
        }
    )


@app.route("/api/multicam/transcribe", methods=["POST"])
def transcribe_multicam():
    """转录机位1的音频（异步后台线程）"""
    data = request.json
    video_path = data.get("video_path")

    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "Video not found"}), 404

    project_id = data.get("project_id", "unknown")

    # 初始化进度
    transcribe_progress[project_id] = {"percent": 0, "status": "running", "error": None}

    def _do_transcribe():
        try:
            # 提取音频
            audio_path = os.path.join(OUTPUT_FOLDER, f"mc_{project_id}_audio.wav")
            if not extract_audio(video_path, audio_path):
                transcribe_progress[project_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Audio extraction failed",
                }
                return

            # 进度回调
            def progress_callback(p):
                transcribe_progress[project_id]["percent"] = p

            # 转录
            subtitles = transcribe_with_whisper(
                audio_path, project_id=project_id, progress_callback=progress_callback
            )

            if not subtitles:
                transcribe_progress[project_id] = {
                    "percent": 0,
                    "status": "error",
                    "error": "Transcription failed",
                }
                return

            # 保存字幕到文件
            json_path = os.path.join(OUTPUT_FOLDER, f"mc_{project_id}_subtitles.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(subtitles, f, ensure_ascii=False, indent=2)

            # 存储到内存，供批量匹配使用
            subtitle_storage[project_id] = subtitles

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


@app.route("/api/multicam/transcribe-progress", methods=["POST"])
def get_transcribe_progress():
    """查询转录进度"""
    data = request.json
    project_id = data.get("project_id", "")
    progress = transcribe_progress.get(
        project_id, {"percent": 0, "status": "unknown", "error": None}
    )
    return jsonify(progress)


@app.route("/api/multicam/subtitles", methods=["GET"])
def get_subtitles():
    """获取项目的字幕数据"""
    project_id = request.args.get("project_id", "")
    
    if not project_id:
        return jsonify({"error": "Missing project_id"}), 400
    
    # 从内存或文件获取字幕
    subtitles = subtitle_storage.get(project_id, [])
    if not subtitles:
        json_path = os.path.join(OUTPUT_FOLDER, f"mc_{project_id}_subtitles.json")
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                subtitles = json.load(f)
            subtitle_storage[project_id] = subtitles
    
    return jsonify({"success": True, "subtitles": subtitles})


@app.route("/api/multicam/bulk-match", methods=["POST", "OPTIONS"])
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
            json_path = os.path.join(OUTPUT_FOLDER, f"mc_{project_id}_subtitles.json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    subtitles = json.load(f)
                subtitle_storage[project_id] = subtitles
            else:
                return jsonify({"error": "Subtitles not found for this project"}), 404

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
                confidence = calculate_similarity(sentence, seg_text) / 100.0
                if confidence >= 0.3:  # 较低的阈值以捕获更多候选
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

            # 过滤：只保留置信度 >= 0.6 的，如果没有则标记为未匹配
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


@app.route("/api/multicam/search", methods=["POST", "OPTIONS"])
def search_quotes():
    """搜索台词时间点"""
    if request.method == "OPTIONS":
        return "", 200

    try:
        data = request.json
        quotes = data.get("quotes", [])
        subtitles = data.get("subtitles", [])

        print(
            f"[DEBUG] Search request: {len(quotes)} quotes, {len(subtitles)} subtitles"
        )
        if subtitles:
            print(f"[DEBUG] Sample subtitle: {subtitles[0]}")

        if not quotes or not subtitles:
            return jsonify({"error": "Missing quotes or subtitles"}), 400

        results = search_quotes_in_order(quotes, subtitles)

        found_count = sum(1 for r in results if r["found"])
        return jsonify(
            {
                "success": True,
                "total": len(results),
                "found": found_count,
                "results": results,
            }
        )
    except Exception as e:
        import traceback

        print(f"[ERROR] search exception: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/concat", methods=["POST"])
def concat_video():
    """拼接视频（多画面）"""
    try:
        data = request.json
        video_paths = data.get("video_paths", [])
        segment_times = data.get("segment_times", [])  # [(start, end), ...]
        project_id = data.get("project_id", "unknown")

        print(
            f"[DEBUG] concat request: video_paths={video_paths}, segment_times={segment_times}"
        )

        if len(video_paths) < 1 or not segment_times:
            return jsonify({"error": "Missing video_paths or segment_times"}), 400

        output_path = os.path.join(OUTPUT_FOLDER, f"mc_{project_id}_final.mp4")

        success = create_multiview(video_paths, segment_times, output_path)

        if success:
            return jsonify({"success": True, "output_path": output_path})
        else:
            return jsonify({"error": "Video concatenation failed"}), 500
    except Exception as e:
        import traceback

        print(f"[ERROR] concat exception: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/multicam/export-fcpxml", methods=["POST"])
def export_fcpxml():
    """导出FCPXML - 预裁剪片段并引用小文件，确保剪映兼容"""
    data = request.json
    video_paths = data.get("video_paths", [])
    segment_times = data.get("segment_times", [])
    project_name = data.get("project_name", "multicam_project")

    print(f"[DEBUG] export_fcpxml: segment_times={segment_times}")

    if len(video_paths) < 1 or not segment_times:
        return jsonify({"error": "Missing video_paths or segment_times"}), 400

    # 自动检测第一个视频的尺寸
    video_info = get_video_info(video_paths[0])
    width = video_info.get("width", 1920)
    height = video_info.get("height", 1080)
    fps = int(video_info.get("fps", 30))

    mac_video_path = data.get("mac_video_path", None)
    fcpxml_content = generate_fcpxml(
        project_name,
        video_paths,
        segment_times,
        output_width=width,
        output_height=height,
        fps=fps,
        mac_video_path=mac_video_path,
    )

    xml_path = os.path.join(OUTPUT_FOLDER, f"{project_name}.fcpxml")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(fcpxml_content)

    print(f"[DEBUG] FCPXML generated: {xml_path}")
    return jsonify({"success": True, "xml_path": xml_path})


@app.route("/api/multicam/export-prproj", methods=["POST"])
def export_prproj():
    """导出Premiere Pro工程文件"""
    data = request.json
    video_paths = data.get("video_paths", [])
    segment_times = data.get("segment_times", [])
    project_name = data.get("project_name", "multicam_project")

    if len(video_paths) < 1 or not segment_times:
        return jsonify({"error": "Missing video_paths or segment_times"}), 400

    # 自动检测第一个视频的尺寸
    video_info = get_video_info(video_paths[0])
    width = video_info.get("width", 1920)
    height = video_info.get("height", 1080)
    fps = video_info.get("fps", 30)

    # Premiere Pro 支持导入标准 FCPXML
    mac_video_path = data.get("mac_video_path", None)
    prproj_content = generate_fcpxml(
        project_name,
        video_paths,
        segment_times,
        output_width=width,
        output_height=height,
        fps=fps,
        mac_video_path=mac_video_path,
    )

    prproj_path = os.path.join(OUTPUT_FOLDER, f"{project_name}.xml")
    with open(prproj_path, "w", encoding="utf-8") as f:
        f.write(prproj_content)

    return jsonify({"success": True, "prproj_path": prproj_path})


@app.route("/api/multicam/export-edl", methods=["POST"])
def export_edl():
    """导出标准 CMX3600 EDL 文件"""
    data = request.json
    video_paths = data.get("video_paths", [])
    segment_times = data.get("segment_times", [])
    project_name = data.get("project_name", "multicam_project")

    if len(video_paths) < 1 or not segment_times:
        return jsonify({"error": "Missing video_paths or segment_times"}), 400

    video_info = get_video_info(video_paths[0])
    fps = int(video_info.get("fps", 30))

    edl_content = generate_edl(
        project_name,
        video_paths,
        segment_times,
        fps=fps,
    )

    edl_path = os.path.join(OUTPUT_FOLDER, f"{project_name}.edl")
    with open(edl_path, "w", encoding="utf-8") as f:
        f.write(edl_content)

    return jsonify({"success": True, "edl_path": edl_path})


if __name__ == "__main__":
    print("=" * 50)
    print("多机位视频字幕拼接处理器")
    print(f"FFmpeg: {FFMPEG_PATH}")
    print(f"Whisper Cache: {CACHE_DIR}")
    print("=" * 50)

    # 添加静态文件路由
    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.route("/frontend/<path:filename>")
    def frontend_files(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    @app.route("/outputs/<path:filename>")
    def output_files(filename):
        return send_from_directory(OUTPUT_FOLDER, filename)

    @app.route("/uploads/<path:filename>")
    def upload_files(filename):
        return send_from_directory(UPLOAD_FOLDER, filename)

    app.run(host="0.0.0.0", port=5003, debug=True)
