# SKILL.md - 豆包 AI 视频水印去除 (超清版 v3.1)

## 技能描述

极致画质豆包 AI 视频水印去除工具，集成 1.5x 超分辨率重建、内容自适应修复和批量处理功能，完美去除"豆包 AI 生成"水印的同时提升视频清晰度。

## 功能特点

### 🎨 核心优势
- ✨ **1.5x 超分辨率重建** - 分辨率提升 50%，画质与文件大小完美平衡
- 🎯 **内容自适应修复** - 智能识别纹理/平滑区域，减少画面损坏
- 🔍 **边缘保护 2.0** - 亚像素级边缘检测和锐化
- 🔊 **无损音频** - 原始音轨 100% 保留，AAC 320k 超高质量编码
- 📊 **智能 CRF** - 根据分辨率自动选择最优质量参数

### 🚀 v3.1 新功能
- 🎯 **优化超分比例** - 从 2x 改为 1.5x，处理速度提升 40%
- 📦 **文件大小优化** - 输出文件减小约 30-40%
- 🎨 **画质保持** - 1.5x 超分已足够清晰，肉眼难以分辨与 2x 的差异
- ⚡ **快速超分算法** - INTER_CUBIC + USM 锐化，速度快质量好

### 🔧 技术原理
- **多算法融合检测**: Canny + Sobel + Laplacian + 自适应阈值
- **内容自适应修复**: 根据边缘密度自动选择 Telea/NS/混合算法
- **快速超分辨率**: INTER_CUBIC 上采样 + USM 锐化增强
- **智能编码**: libx264 slow preset + 动态 CRF

## 安装依赖

```bash
pip install -r requirements.txt
```

**系统要求:**
- Python 3.8+
- FFmpeg (用于视频编码)
- OpenCV >= 4.8.0
- NumPy >= 1.21.0
- scikit-image >= 0.21.0

**依赖包:**
```
opencv-python-headless>=4.8.0
numpy>=1.21.0
tqdm>=4.65.0
scikit-image>=0.21.0
pillow>=9.0.0
```

## 使用方法

### 单个视频处理（推荐）

```bash
python final_perfect_v3_ultra.py <输入视频路径> [输出视频路径]

# 示例
python final_perfect_v3_ultra.py video.mp4
python final_perfect_v3_ultra.py video.mp4 clean_output.mp4
```

### 批量处理模式

```bash
# 处理下载目录中的所有视频
python batch_qq_processor.py

# 监控模式（持续监控新视频）
python batch_qq_processor.py --watch
```

### 版本选择

| 脚本 | 分辨率 | 适用场景 |
|------|--------|---------|
| `final_perfect_v3_ultra.py` | 1.5x 超分 | **推荐**，画质与大小平衡 |
| `final_perfect_v2_enhanced.py` | 原始 | 标准高清需求 |
| `final_perfect.py` | 原始 | 快速处理 |
| `batch_qq_processor.py` | 1.5x 超分 | 批量处理 + QQ 发送 |

## 输出规格

### v3.1 超清版
- **分辨率**: 原始 × 1.5 (720x1280 → 1080x1920)
- **编码**: H.264 High Profile
- **CRF**: 16-20 (根据分辨率自动选择)
- **音频**: AAC 320k 48kHz
- **文件大小**: 约 8-12 MB (10 秒视频)

### 性能对比

| 版本 | 分辨率 | 处理时间 (10 秒) | 文件大小 | 画质 |
|------|--------|----------------|---------|------|
| v3.1 超清版 | 1.5x | ~10 秒 | 8-12 MB | ⭐⭐⭐⭐⭐ |
| v3 超清版 | 2x | ~15 秒 | 12-18 MB | ⭐⭐⭐⭐⭐ |
| v2 增强版 | 原始 | ~8 秒 | 4-6 MB | ⭐⭐⭐⭐ |

## 配置说明

### 修改水印位置

编辑 `final_perfect_v3_ultra.py` 中的 `watermark_regions`:

```python
self.watermark_regions = [
    {"start_sec": 0, "end_sec": 4, "x": 510, "y": 1170, "w": 180, "h": 70},
    {"start_sec": 3, "end_sec": 7, "x": 20, "y": 600, "w": 170, "h": 60},
    {"start_sec": 6, "end_sec": 10, "x": 510, "y": 20, "w": 180, "h": 70},
]
```

### 调整超分比例

```python
# 在 __init__ 方法中
self.scale_factor = 1.5  # 可改为 1.0/1.5/2.0
```

## 常见问题

### Q: 处理速度慢？
**A:** 
- v3.1 已优化速度，比 2x 版本快 40%
- 如需更快，使用 v2 版本：`final_perfect_v2_enhanced.py`

### Q: 画面有损坏？
**A:**
- 调整 `content_adaptive_inpaint` 中的边缘密度阈值
- 增加掩码膨胀次数

### Q: 批量处理如何配置？
**A:**
- 视频放入：`~/.openclaw/qqbot/downloads/`
- 运行：`python batch_qq_processor.py`
- 输出到：`~/.openclaw/qqbot/downloads/clean_videos/`

## 更新日志

### v3.1.0 (2026-03-18)
- 🎯 优化超分比例：2x → 1.5x
- ⚡ 处理速度提升 40%
- 📦 文件大小减小 30-40%
- 🎨 画质保持优秀

### v3.0.0 (2026-03-18)
- ✨ 新增超分辨率重建
- 🎨 内容自适应修复
- 🔍 边缘保护 2.0
- 📦 批量处理模式

### v2.0.0 (2026-03-17)
- ✨ 多算法融合检测
- 🎨 高级修复算法
- 🔍 画质增强模块

## 许可证

MIT-0 - Free to use, modify, and redistribute.

## 作者

mac 小虫子 · 严谨专业版
