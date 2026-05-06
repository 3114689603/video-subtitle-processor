"""
轻量级说话人分离模块
不需要 PyAnnote，使用简单的音频特征分析实现基础说话人分离
"""

import numpy as np
import librosa
from sklearn.cluster import KMeans
from typing import List, Dict, Tuple
import tempfile
import os


def extract_audio_features(audio_path: str, sr: int = 16000) -> np.ndarray:
    """提取音频特征用于说话人分离"""
    try:
        # 加载音频
        y, sr = librosa.load(audio_path, sr=sr, mono=True)
        
        # 提取 MFCC 特征
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20)
        
        # 提取其他特征
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
        zero_crossing_rate = librosa.feature.zero_crossing_rate(y)
        
        # 合并特征
        features = np.vstack([
            mfcc,
            chroma,
            spectral_centroid,
            spectral_rolloff,
            zero_crossing_rate
        ])
        
        return features.T
    except Exception as e:
        print(f"[ERROR] Feature extraction failed: {e}")
        return None


def simple_speaker_diarization(audio_path: str, num_speakers: int = 2) -> List[Dict]:
    """
    简单的说话人分离实现
    使用 K-means 聚类基于音频特征区分说话人
    """
    try:
        # 提取特征
        features = extract_audio_features(audio_path)
        if features is None:
            return []
        
        # 归一化
        features = (features - features.mean(axis=0)) / (features.std(axis=0) + 1e-8)
        
        # 降维到主要特征
        from sklearn.decomposition import PCA
        pca = PCA(n_components=min(10, features.shape[1]))
        features_reduced = pca.fit_transform(features)
        
        # K-means 聚类
        kmeans = KMeans(n_clusters=num_speakers, random_state=42, n_init=10)
        labels = kmeans.fit_predict(features_reduced)
        
        # 获取音频时长
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
        total_duration = len(y) / sr
        
        # 将帧级别的标签转换为时间段
        hop_length = 512
        frame_duration = hop_length / sr
        
        segments = []
        current_speaker = labels[0]
        start_time = 0
        
        for i, label in enumerate(labels):
            time = i * frame_duration
            
            if label != current_speaker:
                # 保存上一段
                if time - start_time > 0.5:  # 最少0.5秒
                    segments.append({
                        "start": round(start_time, 2),
                        "end": round(time, 2),
                        "speaker": f"SPEAKER_{current_speaker:02d}"
                    })
                start_time = time
                current_speaker = label
        
        # 添加最后一段
        if total_duration - start_time > 0.5:
            segments.append({
                "start": round(start_time, 2),
                "end": round(total_duration, 2),
                "speaker": f"SPEAKER_{current_speaker:02d}"
            })
        
        # 合并相邻的同说话人段
        segments = merge_segments(segments)
        
        return segments
        
    except Exception as e:
        print(f"[ERROR] Diarization failed: {e}")
        return []


def merge_segments(segments: List[Dict], gap_threshold: float = 1.0) -> List[Dict]:
    """合并相邻的同说话人段"""
    if not segments:
        return []
    
    merged = [segments[0]]
    
    for seg in segments[1:]:
        last = merged[-1]
        
        if seg["speaker"] == last["speaker"] and seg["start"] - last["end"] < gap_threshold:
            # 合并
            last["end"] = seg["end"]
        else:
            merged.append(seg)
    
    return merged


def assign_speakers_to_subtitles(subtitles: List[Dict], speaker_segments: List[Dict]) -> List[Dict]:
    """将说话人信息分配给字幕"""
    if not speaker_segments:
        # 如果没有说话人信息，使用简单的轮询分配
        for i, sub in enumerate(subtitles):
            sub["speaker"] = f"SPEAKER_{i % 2:02d}"
        return subtitles
    
    for sub in subtitles:
        sub_start = sub["start"]
        sub_end = sub["end"]
        sub_mid = (sub_start + sub_end) / 2
        
        # 找到包含该字幕中间的说话人
        best_speaker = "SPEAKER_00"
        best_overlap = 0
        
        for seg in speaker_segments:
            overlap_start = max(sub_start, seg["start"])
            overlap_end = min(sub_end, seg["end"])
            overlap = max(0, overlap_end - overlap_start)
            
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = seg["speaker"]
        
        sub["speaker"] = best_speaker
    
    return subtitles


# 兼容 PyAnnote 的 API 接口
class SimpleDiarizationPipeline:
    """简单的说话人分离管道，兼容 PyAnnote API"""
    
    def __init__(self, num_speakers: int = 2):
        self.num_speakers = num_speakers
    
    def __call__(self, audio_path: str, num_speakers: int = None) -> List[Dict]:
        """执行说话人分离"""
        n_speakers = num_speakers or self.num_speakers
        return simple_speaker_diarization(audio_path, n_speakers)


def create_pipeline(num_speakers: int = 2) -> SimpleDiarizationPipeline:
    """创建说话人分离管道"""
    return SimpleDiarizationPipeline(num_speakers)


# 如果 librosa 不可用，提供一个更简单的版本
def extract_audio_features_simple(audio_path: str, sr: int = 16000) -> np.ndarray:
    """简化的特征提取，使用 numpy 和 scipy"""
    try:
        import soundfile as sf
        
        # 加载音频
        y, sr = sf.read(audio_path)
        if len(y.shape) > 1:
            y = y.mean(axis=1)  # 转为单声道
        
        # 重采样到目标采样率
        if sr != 16000:
            from scipy import signal
            num_samples = int(len(y) * 16000 / sr)
            y = signal.resample(y, num_samples)
            sr = 16000
        
        # 提取简单的时域特征
        frame_length = int(sr * 0.025)  # 25ms
        hop_length = int(sr * 0.01)     # 10ms
        
        # 分帧
        frames = []
        for i in range(0, len(y) - frame_length, hop_length):
            frame = y[i:i + frame_length]
            
            # 简单特征：能量、过零率
            energy = np.sum(frame ** 2)
            zcr = np.sum(np.diff(np.sign(frame)) != 0)
            
            frames.append([energy, zcr])
        
        return np.array(frames)
        
    except ImportError:
        print("[WARNING] soundfile not available, using basic features")
        return None


if __name__ == "__main__":
    # 测试
    print("Speaker Diarization Module")
    print("Usage: from speaker_diarization import create_pipeline")
    print("       pipeline = create_pipeline(num_speakers=2)")
    print("       segments = pipeline('audio.wav')")
