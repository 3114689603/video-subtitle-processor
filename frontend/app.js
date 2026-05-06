// 视频字幕处理器 - 统一前端 (单视频 + 多机位)

const API_BASE_URL = 'http://localhost:5000/api';

// 全局状态
let currentMode = 'single';
let isOfflineMode = false;

// 单视频模式状态
let singleFileId = null;
let singleSubtitles = [];

// 多机位模式状态
let currentProject = null;
let multiCameras = [];
let multiSubtitles = [];
let uploadedFiles = {};

// ==================== 初始化 ====================

document.addEventListener('DOMContentLoaded', () => {
    checkApiStatus();
    setInterval(checkApiStatus, 5000); // 每5秒检查一次API状态
});

async function checkApiStatus() {
    const statusEl = document.getElementById('apiStatus');
    const statusText = document.getElementById('apiStatusText');
    
    try {
        const response = await fetch(`${API_BASE_URL}/health`, { 
            method: 'GET',
            cache: 'no-cache'
        });
        
        if (response.ok) {
            statusEl.className = 'api-status connected';
            statusText.textContent = 'API已连接';
            isOfflineMode = false;
            return true;
        }
    } catch (e) {
        console.log('API not available:', e);
    }
    
    statusEl.className = 'api-status error';
    statusText.textContent = 'API未连接';
    isOfflineMode = true;
    return false;
}

// ==================== 模式切换 ====================

function switchMode(mode) {
    currentMode = mode;
    
    // 更新按钮状态
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    
    // 显示对应内容
    document.querySelectorAll('.content-area').forEach(area => {
        area.classList.remove('active');
    });
    document.getElementById(`${mode}-mode`).classList.add('active');
    
    showToast(`已切换到${mode === 'single' ? '单视频' : '多机位'}模式`, 'info');
}

// ==================== 单视频模式 ====================

async function handleSingleUpload(input) {
    const file = input.files[0];
    if (!file) return;
    
    // 验证文件类型
    const allowedTypes = ['video/mp4', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska'];
    if (!allowedTypes.includes(file.type)) {
        showToast('不支持的文件格式', 'error');
        return;
    }
    
    // 显示进度
    document.getElementById('singleProgress').style.display = 'block';
    updateProgress('singleProgressFill', 'singleProgressText', 10, '正在上传...');
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        // 上传
        updateProgress('singleProgressFill', 'singleProgressText', 30, '上传中...');
        const uploadRes = await fetch(`${API_BASE_URL}/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (!uploadRes.ok) throw new Error('上传失败');
        const uploadData = await uploadRes.json();
        singleFileId = uploadData.file_id;
        
        // 处理
        updateProgress('singleProgressFill', 'singleProgressText', 50, '正在识别语音...');
        const processRes = await fetch(`${API_BASE_URL}/process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_path: uploadData.video_path,
                file_id: singleFileId
            })
        });
        
        if (!processRes.ok) throw new Error('处理失败');
        const processData = await processRes.json();
        
        singleSubtitles = processData.subtitles;
        
        // 更新UI
        updateProgress('singleProgressFill', 'singleProgressText', 100, '完成！');
        
        document.getElementById('singleStats').style.display = 'grid';
        document.getElementById('singleDuration').textContent = formatTime(processData.stats.total_duration);
        document.getElementById('singleSubtitles').textContent = processData.stats.subtitle_count;
        document.getElementById('singleSpeakers').textContent = processData.stats.speaker_count;
        
        renderSingleSubtitles();
        
        document.getElementById('singleSubtitlesCard').style.display = 'block';
        document.getElementById('singleExportCard').style.display = 'block';
        
        showToast('字幕生成成功！', 'success');
        
    } catch (error) {
        showToast('处理失败: ' + error.message, 'error');
    } finally {
        setTimeout(() => {
            document.getElementById('singleProgress').style.display = 'none';
        }, 1000);
    }
}

function renderSingleSubtitles(highlightText = '') {
    const container = document.getElementById('singleSubtitleList');
    
    if (!singleSubtitles || singleSubtitles.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-film"></i>
                <p>暂无字幕</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = singleSubtitles.map(sub => {
        let text = sub.text;
        if (highlightText) {
            const regex = new RegExp(`(${highlightText})`, 'gi');
            text = text.replace(regex, '<mark style="background: rgba(249, 115, 22, 0.2); color: var(--accent);">$1</mark>');
        }
        
        return `
            <div class="subtitle-item">
                <div class="subtitle-time">${formatTime(sub.start)}</div>
                <div class="subtitle-text">
                    <span class="subtitle-speaker">${sub.speaker || 'SPEAKER_00'}</span>
                    ${text}
                </div>
            </div>
        `;
    }).join('');
}

function searchSingleSubtitles() {
    const keyword = document.getElementById('singleSearch').value.trim();
    renderSingleSubtitles(keyword);
}

function exportSingle(format) {
    if (!singleFileId) {
        showToast('请先上传视频', 'error');
        return;
    }
    
    window.open(`${API_BASE_URL}/export/${singleFileId}?format=${format}`, '_blank');
    showToast('正在导出...', 'success');
}

// ==================== 多机位模式 ====================

function createProject() {
    const name = document.getElementById('projectName').value.trim() || `项目_${Date.now()}`;
    const count = parseInt(document.getElementById('cameraCount').value) || 2;
    
    currentProject = { name, count, id: Date.now().toString() };
    multiCameras = [];
    uploadedFiles = {};
    
    // 生成机位卡片
    const grid = document.getElementById('cameraGrid');
    grid.innerHTML = '';
    
    for (let i = 0; i < count; i++) {
        const card = document.createElement('div');
        card.className = 'camera-card';
        card.id = `camera-${i}`;
        card.innerHTML = `
            <i class="fas fa-video"></i>
            <h4>机位 ${i + 1}</h4>
            <p style="font-size: 0.8rem; color: var(--text-light);">点击上传</p>
            <input type="file" id="camera-input-${i}" accept="video/*" style="display: none;" 
                   onchange="handleCameraUpload(${i}, this)">
        `;
        card.onclick = () => document.getElementById(`camera-input-${i}`).click();
        grid.appendChild(card);
    }
    
    document.getElementById('multiSetupCard').style.display = 'none';
    document.getElementById('multiCameraCard').style.display = 'block';
    
    showToast(`已创建 ${count} 机位项目`, 'success');
}

async function handleCameraUpload(index, input) {
    const file = input.files[0];
    if (!file) return;
    
    const card = document.getElementById(`camera-${index}`);
    card.classList.add('uploaded');
    card.innerHTML = `
        <i class="fas fa-check-circle" style="color: var(--success);"></i>
        <h4>${file.name}</h4>
        <p style="font-size: 0.8rem; color: var(--success);">已上传</p>
    `;
    
    uploadedFiles[index] = file;
    
    // 检查是否全部上传
    if (Object.keys(uploadedFiles).length === currentProject.count) {
        document.getElementById('processMultiBtn').disabled = false;
    }
    
    showToast(`机位 ${index + 1} 上传成功`, 'success');
}

async function processMultiProject() {
    const btn = document.getElementById('processMultiBtn');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 处理中...';
    
    showToast('开始处理多机位视频...', 'info');
    
    try {
        // 先创建项目
        const createRes = await fetch(`${API_BASE_URL}/multi/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: currentProject.name
            })
        });
        
        if (!createRes.ok) throw new Error('创建项目失败');
        const createData = await createRes.json();
        currentProject.id = createData.project_id;
        
        // 上传每个机位
        for (let i = 0; i < currentProject.count; i++) {
            if (uploadedFiles[i]) {
                const formData = new FormData();
                formData.append('file', uploadedFiles[i]);
                formData.append('name', `机位 ${i + 1}`);
                formData.append('index', i);
                
                showToast(`上传机位 ${i + 1}...`, 'info');
                
                await fetch(`${API_BASE_URL}/multi/projects/${currentProject.id}/cameras`, {
                    method: 'POST',
                    body: formData
                });
            }
        }
        
        // 开始处理
        showToast('正在识别语音...', 'info');
        const processRes = await fetch(`${API_BASE_URL}/multi/projects/${currentProject.id}/process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!processRes.ok) throw new Error('处理失败');
        const processData = await processRes.json();
        
        multiSubtitles = processData.project.subtitles || [];
        
        renderMultiSubtitles();
        
        document.getElementById('multiCameraCard').style.display = 'none';
        document.getElementById('multiSubtitlesCard').style.display = 'block';
        document.getElementById('multiExportCard').style.display = 'block';
        
        showToast('处理完成！', 'success');
        
    } catch (error) {
        showToast('处理失败: ' + error.message, 'error');
        // 使用模拟数据作为回退
        multiSubtitles = generateMockMultiSubtitles();
        renderMultiSubtitles();
        document.getElementById('multiCameraCard').style.display = 'none';
        document.getElementById('multiSubtitlesCard').style.display = 'block';
        document.getElementById('multiExportCard').style.display = 'block';
    } finally {
        btn.innerHTML = '<i class="fas fa-magic"></i> 开始处理';
    }
}

function generateMockMultiSubtitles() {
    const texts = [
        "大家好，欢迎来到本次访谈",
        "很高兴能和大家一起交流",
        "今天我们要讨论的话题是",
        "首先请嘉宾介绍一下自己",
        "我是一名从事这个行业十年的从业者"
    ];
    
    const subtitles = [];
    for (let i = 0; i < 10; i++) {
        subtitles.push({
            id: i,
            start: i * 5,
            end: i * 5 + 4,
            text: texts[i % texts.length],
            speaker: `SPEAKER_${i % 2}`,
            camera: `机位 ${(i % currentProject.count) + 1}`
        });
    }
    return subtitles;
}

function renderMultiSubtitles(highlightText = '') {
    const container = document.getElementById('multiSubtitleList');
    
    if (!multiSubtitles || multiSubtitles.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-film"></i>
                <p>暂无字幕</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = multiSubtitles.map(sub => {
        let text = sub.text;
        if (highlightText) {
            const regex = new RegExp(`(${highlightText})`, 'gi');
            text = text.replace(regex, '<mark style="background: rgba(249, 115, 22, 0.2); color: var(--accent);">$1</mark>');
        }
        
        return `
            <div class="subtitle-item">
                <div class="subtitle-time">${formatTime(sub.start)}</div>
                <div class="subtitle-text">
                    <span class="subtitle-speaker" style="background: var(--primary);">${sub.camera}</span>
                    <span class="subtitle-speaker">${sub.speaker}</span>
                    ${text}
                </div>
            </div>
        `;
    }).join('');
}

function searchMultiSubtitles() {
    const keyword = document.getElementById('multiSearch').value.trim();
    renderMultiSubtitles(keyword);
}

function exportMulti(format) {
    if (!currentProject || !currentProject.id) {
        showToast('请先处理项目', 'error');
        return;
    }
    
    window.open(`${API_BASE_URL}/multi/projects/${currentProject.id}/export?format=${format}`, '_blank');
    showToast(`正在导出 ${format.toUpperCase()}...`, 'success');
}

// ==================== 工具函数 ====================

function updateProgress(fillId, textId, percent, text) {
    document.getElementById(fillId).style.width = percent + '%';
    document.getElementById(textId).textContent = text;
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icon = type === 'success' ? 'check-circle' : 
                 type === 'error' ? 'exclamation-circle' : 'info-circle';
    
    toast.innerHTML = `<i class="fas fa-${icon}"></i><span>${message}</span>`;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}
